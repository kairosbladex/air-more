from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airmore.service.api import create_app
from airmore.service.config import Settings
from airmore.service.models import ReportError, ReportRequest
from airmore.service.store import JobStore
from airmore.service.worker import worker_lock


@pytest.fixture
def api(tmp_path):
    cfg = Settings(state_dir=tmp_path, clients={"backend": "a" * 32, "other": "b" * 32})
    return TestClient(create_app(cfg))


def payload(**changes):
    return dict(siteIds=["hangar"], reportType="DAY", startDate="2026-08-01",
                endDate="2026-08-03") | changes


def headers(**changes):
    return {"Authorization": "Bearer " + "a" * 32, "Idempotency-Key": "request-1",
            "X-Requester-Id": "reader"} | changes


def test_auth_and_idempotency(api):
    assert api.post("/api/v1/air-reports", json=payload()).status_code == 401
    created = api.post("/api/v1/air-reports", json=payload(), headers=headers())
    assert created.status_code == 202, created.text
    item = created.json()
    assert item["status"] == "queued"
    assert api.post("/api/v1/air-reports", json=payload(), headers=headers()).json()["report_id"] == item["report_id"]
    assert api.post("/api/v1/air-reports", json=payload(), headers=headers(**{"X-Requester-Id":"someone"})).status_code == 409
    endpoint = "/api/v1/air-reports/" + item["report_id"]
    assert api.get(endpoint, headers=headers()).status_code == 200
    assert api.get(endpoint, headers=headers(**{"X-Requester-Id": "someone"})).status_code == 404
    assert api.get(endpoint, headers=headers(Authorization="Bearer " + "b" * 32)).status_code == 404
    assert api.get(endpoint + "/files/docx", headers=headers()).status_code == 409
    assert api.get(endpoint + "/files/json", headers=headers()).status_code == 422


@pytest.mark.parametrize("change", [
    {"startDate": "2026-09-01"}, {"startDate": "2026-08-01T00:00:00"}, {"startDate": 0},
    {"siteIds": []}, {"siteIds": ["a", "a"]}, {"project_id": "' OR 1=1"},
    {"model": "untrusted"}, {"output_dir": "/tmp"}, {"endDate": "2028-08-01"},
    {"siteIds": [f"h{i}" for i in range(51)]},
])
def test_bad_requests(api, change):
    assert api.post("/api/v1/air-reports", json=payload(**change), headers=headers()).status_code == 422


def test_missing_idempotency_and_requester(api):
    assert api.post("/api/v1/air-reports", json=payload(), headers={"Authorization": "Bearer " + "a" * 32}).status_code == 422
    row = api.post("/api/v1/air-reports", json=payload(), headers=headers()).json()
    assert api.get("/api/v1/air-reports/" + row["report_id"], headers={"Authorization": "Bearer " + "a" * 32}).status_code == 422


def test_completed_download_is_owned_and_has_no_paths(api):
    item = api.post("/api/v1/air-reports", json=payload(), headers=headers()).json()
    store = api.app.state.store
    store.claim()
    output = store.root / "artifacts" / item["report_id"]
    output.mkdir(parents=True)
    (output / "report.pdf").write_bytes(b"%PDF- test")
    store.finish(item["report_id"], result={"gaps": [], "summary": {"sample_count": 3}})
    endpoint = "/api/v1/air-reports/" + item["report_id"]
    body = api.get(endpoint, headers=headers()).json()
    assert str(store.root) not in str(body)
    assert api.get(endpoint + "/files/pdf", headers=headers()).content == b"%PDF- test"
    assert api.get(endpoint + "/files/pdf", headers=headers(**{"X-Requester-Id": "stranger"})).status_code == 404
    (output / "report.pdf").unlink()
    (output / "report.pdf").symlink_to(store.path)
    assert api.get(endpoint + "/files/pdf", headers=headers()).status_code == 404


def test_concurrent_idempotency_and_claim(tmp_path):
    store = JobStore(tmp_path)
    request = ReportRequest(project_id="project", hangar_ids=["hangar"], start_date="2026-08-01", end_date="2026-08-03", requester_id="reader")
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: store.create("backend", "key", request), range(12)))
    assert len({r["id"] for r in rows}) == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(6)))
    assert sum(c is not None for c in claims) == 1
    store.recover()
    assert store.public(store.owned(rows[0]["id"], "backend", "reader"))["error"]["code"] == "WORKER_INTERRUPTED"
    assert store.claim() is None
    with worker_lock(tmp_path), pytest.raises(RuntimeError):
        with worker_lock(tmp_path):
            pass


def test_openapi_and_no_anonymous_start(tmp_path):
    with pytest.raises(ValueError):
        create_app(Settings(state_dir=tmp_path))
    app = create_app(Settings(state_dir=tmp_path, clients={"test": "t" * 32}))
    assert len(app.openapi()["paths"]) == 3

@pytest.mark.parametrize('kind', ['DAY','WEEK','MONTH'])
def test_confirmed_contract_and_exact_period(api, kind):
    import json
    body={'siteIds':['site-2','site-1'],'reportType':kind,'startDate':'2026-08-31','endDate':'2026-09-01'}
    response=api.post('/api/v1/air-reports',json=body,headers=headers())
    assert response.status_code==202
    row=api.app.state.store.owned(response.json()['report_id'],'backend','reader')
    internal=ReportRequest.model_validate_json(row['request'])
    assert internal.days==2 and internal.project_id is None and internal.report_type==kind
    assert str(internal.end_date)=='2026-09-01'
    assert internal.query_bounds()[1].isoformat()=='2026-09-02T00:00:00'
    body['siteIds'].reverse()
    assert api.post('/api/v1/air-reports',json=body,headers=headers()).json()['report_id']==row['id']
    body['reportType']='DAY' if kind!='DAY' else 'WEEK'
    assert api.post('/api/v1/air-reports',json=body,headers=headers()).status_code==409

@pytest.mark.parametrize('value',['day','YEAR','',None])
def test_report_type_rejected(api,value):
    assert api.post('/api/v1/air-reports',json=payload(reportType=value),headers=headers()).status_code==422

def test_four_fields_required_and_openapi(api):
    body=payload();body.pop('reportType')
    assert api.post('/api/v1/air-reports',json=body,headers=headers()).status_code==422
    h=headers();h.pop('X-Requester-Id')
    assert api.post('/api/v1/air-reports',json=payload(),headers=h).status_code==422
    schema=api.app.openapi()['components']['schemas']['CreateReportRequest']
    assert set(schema['properties'])==set(schema['required'])=={'siteIds','reportType','startDate','endDate'}
