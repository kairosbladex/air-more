import io
import json
import socket
from contextlib import contextmanager
from dataclasses import asdict

import pytest

from airmore.service.config import Settings
from airmore.service.examples import synthetic_case
from airmore.service.models import Dataset, ReportError
from airmore.service.repository import MySQLRepository
from airmore.service.statistics import build_statistics
from airmore.service.worker import Worker
from airmore.service.writer import OllamaAnalysisWriter, WrittenAnalysis, fact_catalog, validate_composition


def good_body(hangar):
    facts, allowed = fact_catalog(hangar)
    body = {s: ["，".join("{" + f + "}" for f in ids[:2]) + "。"] for s, ids in allowed.items()}
    return body, facts, allowed


class FixtureWriter:
    def write(self, hangar):
        body, facts, allowed = good_body(hangar)
        return WrittenAnalysis(validate_composition(body, facts, allowed), body, facts,
                               {"engine": "test-fixture", "model": "not-a-real-model"})


@pytest.fixture
def case():
    request, dataset, profiles = synthetic_case(1, missing=True)
    h = build_statistics(request, dataset, profiles)["hangars"]["demo-1"]
    return request, dataset, profiles, h


def test_model_binds_complete_claims_not_just_numbers(case):
    *_, h = case
    body, facts, allowed = good_body(h)
    assert "{" not in validate_composition(body, facts, allowed)["overview"]
    for invalid in ["{F001}污染来源为工厂。", "{F001}，AQI为99。", "{F999}。", "{F001}{F001}。"]:
        changed = body | {"overview": [invalid]}
        with pytest.raises(ValueError):
            validate_composition(changed, facts, allowed)
    with pytest.raises(ValueError):
        validate_composition(body | {"overview": ["{F001}。"]}, facts, allowed)
    with pytest.raises(ValueError):
        validate_composition(body | {"advice": ["{F001}。"]}, facts, allowed)


def response_for(body):
    return {"model": "qwen3.5:9b", "done": True, "done_reason": "stop", "message": {"content": json.dumps(body)}}


def test_model_retries_are_bounded(case):
    *_, h = case
    good, _, _ = good_body(h)
    responses = iter([socket.timeout(), response_for({}), response_for(good)])
    calls = []
    def opener(request, timeout):
        calls.append(json.loads(request.data))
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(json.dumps(value).encode())
    result = OllamaAnalysisWriter(Settings(), opener=opener, runtime_probe=lambda: {"digest": "fixture", "size_vram": 100})._write_selection(h)
    assert result.metadata["calls"] == 3
    assert result.metadata["timeout_retries"] == 1 and result.metadata["repairs"] == 1
    assert all("password" not in json.dumps(p).lower() and "lon" not in json.dumps(p) for p in calls)


@pytest.mark.parametrize("mode,expected", [("timeout", "MODEL_TIMEOUT"), ("invalid", "MODEL_VALIDATION_FAILED")])
def test_model_no_silent_template_fallback(case, mode, expected):
    *_, h = case
    calls = []
    def opener(*args, **kwargs):
        calls.append(1)
        if mode == "timeout":
            raise socket.timeout()
        return io.BytesIO(json.dumps(response_for({})).encode())
    with pytest.raises(ReportError) as error:
        OllamaAnalysisWriter(Settings(), opener=opener)._write_selection(h)
    assert error.value.code == expected and len(calls) == 2


class FakeRepository:
    def __init__(self, dataset):
        self.dataset = dataset
    def load(self, request):
        return self.dataset


def fake_render(stats, analyses, composite, cfg, output):
    for fmt in ("docx", "pdf"):
        (output / f"report.{fmt}").write_bytes(b"test" * 100)
    return {"template_version": "test-only"}


def test_worker_atomic_publish_and_evidence(case, tmp_path):
    req, data, profiles, _ = case
    cfg = Settings(state_dir=tmp_path, measurement_profiles=profiles)
    worker = Worker(cfg, FakeRepository(data), FixtureWriter(), fake_render)
    job = worker.store.create("backend", "idem", req)
    assert worker.run(once=True)
    status = worker.store.public(worker.store.owned(job["id"], "backend", req.requester_id))
    assert status["status"] == "succeeded_with_gaps"
    folder = tmp_path / "artifacts" / job["id"]
    evidence = json.loads((folder / "evidence.json").read_text())
    assert evidence["writing"]["demo-1"]["metadata"]["engine"] == "test-fixture"
    assert "samples" not in evidence["statistics"]  # Only aggregate samples counts.
    assert not list((tmp_path / "artifacts").glob(".report-*"))


def test_worker_no_data_and_failure_cleanup(case, tmp_path, caplog):
    req, data, profiles, _ = case
    cfg = Settings(state_dir=tmp_path, measurement_profiles=profiles)
    worker = Worker(cfg, FakeRepository(Dataset([], data.hangars, data_kind='synthetic')), FixtureWriter(), fake_render)
    a = worker.store.create("backend", "empty", req)
    worker.run(once=True)
    assert worker.store.public(worker.store.owned(a["id"], "backend", req.requester_id))["error"]["code"] == "NO_DATA"
    def bad_render(*args):
        (args[-1] / "report.docx").write_bytes(b"partial")
        raise RuntimeError("credentials=SUPER_SECRET")
    worker = Worker(cfg, FakeRepository(data), FixtureWriter(), bad_render)
    b = worker.store.create("backend", "bad", req)
    worker.run(once=True)
    status = worker.store.public(worker.store.owned(b["id"], "backend", req.requester_id))
    assert status["status"] == "failed"
    assert "SUPER_SECRET" not in caplog.text + json.dumps(status)
    assert not list((tmp_path / "artifacts").glob(".report-*"))
    assert not (tmp_path / "artifacts" / b["id"]).exists()


class Cursor:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.sql = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, params=None):
        self.sql.append((sql, params))
    def fetchall(self):
        return next(self.replies)


class Connection:
    def __init__(self, cur):
        self.cur, self.rolled_back, self.closed = cur, False, False
    def cursor(self):
        return self.cur
    def rollback(self):
        self.rolled_back = True
    def close(self):
        self.closed = True


def test_repository_readonly_scoped_and_bounded(case):
    req, _, _, _ = case
    cur = Cursor([[{"id": "demo-1", "name": "测试", "physical_hangar_id": "h", "uav_id": "u", "hangar_deleted": 0, "uav_deleted": 0, "hangar_project": req.project_id, "uav_project": req.project_id}], [{"reason": "missing_task", "n": 4}], [], [], [], [], [], [], []])
    conn = Connection(cur)
    repo = MySQLRepository(Settings(), connect=lambda **kw: conn)
    data = repo.load(req)
    assert data.exclusions == {"missing_task": 4}
    assert conn.rolled_back and conn.closed
    sql = "\n".join(s for s, _ in cur.sql)
    assert "READ ONLY" in sql and "WITH CONSISTENT SNAPSHOT" in sql
    assert "a.time>=%s AND a.time<%s" in sql and "ORDER BY a.time,a.id LIMIT %s" in sql
    assert "a.create_time" not in sql and "20260630" not in sql
    assert "requester_id" not in sql
    assert all(req.project_id not in s for s, _ in cur.sql)


def test_repository_rejects_current_scope_and_row_overflow(case):
    req, _, _, _ = case
    conn = Connection(Cursor([[]]))
    with pytest.raises(ReportError) as error:
        MySQLRepository(Settings(), connect=lambda **kw: conn).load(req)
    assert error.value.code == "INVALID_HANGAR_SCOPE" and conn.rolled_back
    conn = Connection(Cursor([[{"id": "demo-1", "name": "测试", "physical_hangar_id": "h", "uav_id": "u", "hangar_deleted": 0, "uav_deleted": 0, "hangar_project": req.project_id, "uav_project": req.project_id}], [], [{}, {}]]))
    with pytest.raises(ReportError) as error:
        MySQLRepository(Settings(max_rows=1), connect=lambda **kw: conn).load(req)
    assert error.value.code == "TOO_MUCH_DATA"


def test_no_data_preserves_exclusion_summary(case, tmp_path):
    req, data, profiles, _ = case
    data.samples = []
    data.exclusions = {"missing_task": 7}
    worker = Worker(Settings(state_dir=tmp_path), FakeRepository(data), FixtureWriter(), fake_render)
    job = worker.store.create("backend", "empty", req)
    worker.run(once=True)
    result = worker.store.public(worker.store.owned(job["id"], "backend", req.requester_id))
    assert result["error"]["code"] == "NO_DATA"
    assert result["summary"]["excluded_project_rows"]["missing_task"] == 7
    assert not result["files"]

@pytest.mark.parametrize('rows,error', [
    ([], 'INVALID_HANGAR_SCOPE'),
    ([{'id':'h1','project_id':'p'}], 'INVALID_HANGAR_SCOPE'),
    ([{'id':'h1','project_id':'p'},{'id':'h2','project_id':'q'}], 'INVALID_PROJECT_SCOPE'),
    ([{'id':'h1','project_id':None},{'id':'h2','project_id':None}], 'INVALID_PROJECT_SCOPE'),
    ([{'id':'h1','project_id':'p'},{'id':'h2','project_id':'p'}], None),
])
def test_resolve_project_from_current_sites(rows,error):
    cur=Cursor([rows]);conn=Connection(cur);repo=MySQLRepository(Settings(),connect=lambda **kw:conn)
    if error:
        with pytest.raises(ReportError) as exc:repo.resolve_project(['h1','h2'])
        assert exc.value.code==error
    else:assert repo.resolve_project(['h1','h2'])=='p'
    assert conn.rolled_back and conn.closed
    assert cur.sql[-1][1]==('h1','h2') and 'del_flag=0' in cur.sql[-1][0]


def test_worker_resolves_new_request_before_statistics(case,tmp_path):
    req,data,profiles,_=case
    project=req.project_id;req.project_id=None;req.report_type='WEEK'
    class ResolvingRepository(FakeRepository):
        def resolve_project(self,ids):
            assert ids==req.hangar_ids
            return project
        def load(self,request):
            assert request.project_id==project
            return super().load(request)
    worker=Worker(Settings(state_dir=tmp_path,measurement_profiles=profiles),ResolvingRepository(data),FixtureWriter(),fake_render)
    job=worker.store.create('backend','new-contract',req);worker.run(once=True)
    status=worker.store.public(worker.store.owned(job['id'],'backend',req.requester_id))
    assert status['status']=='succeeded_with_gaps' and status['summary']['report_type']=='WEEK'
    evidence=json.loads((tmp_path/'artifacts'/job['id']/'evidence.json').read_text())
    assert evidence['statistics']['request']['project_id']==project
