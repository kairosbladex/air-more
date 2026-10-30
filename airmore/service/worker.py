"""One OS-locked worker per state directory. Never starts on API import."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import shutil
import signal
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .report_index import evaluate as selected_index
from .hangar_scope import group_selected
from .site_forecast import load as load_forecast
from .config import Settings
from .models import ReportError, ReportRequest, TZ
from .repository import MySQLRepository
from .statistics import build_statistics
from .store import JobStore, now
from .writer import OllamaAnalysisWriter, TemplateAnalysisWriter

LOG = logging.getLogger("airmore.worker")


@contextmanager
def worker_lock(root: Path):
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (root / "worker.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("此状态目录已有worker运行") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Worker:
    def __init__(self, cfg: Settings, repository=None, writer=None, renderer=None, forecast_loader=None):
        self.cfg = cfg
        self.store = JobStore(cfg.state_dir)
        self.repository = repository or MySQLRepository(cfg)
        self.writer = writer or (TemplateAnalysisWriter() if cfg.writing_engine == "template" else OllamaAnalysisWriter(cfg))
        self.forecast_loader = forecast_loader or load_forecast
        if renderer is None:
            from .render import render
            renderer = render
        self.renderer = renderer

    def process_one(self):
        job = self.store.claim()
        if job is None:
            return False
        identifier, staging = job["id"], None
        result = None
        try:
            request = ReportRequest.model_validate_json(job["request"])
            if request.as_of is None:
                request.as_of = datetime.now(TZ)
            if request.project_id is None:
                request.project_id = self.repository.resolve_project(request.hangar_ids)
            dataset = self.repository.load(request)
            from .indicators import fetch_indicators
            dataset.project_indicators = fetch_indicators(request.project_id, self.cfg.indicators_projects)
            self.store.stage(identifier, "statistics")
            statistics_request, dataset = group_selected(request, dataset)
            stats = build_statistics(statistics_request, dataset, self.cfg.measurement_profiles)
            stats['source_request'] = request.model_dump(mode='json')
            result = {"gaps": stats["gaps"], "summary": {
                "sample_count": sum(h["current"]["samples"] for h in stats["hangars"].values()),
                "hangar_count": len(stats["hangars"]), "report_type": request.report_type, "rules_version": stats["rules_version"],
                "excluded_project_rows": stats["excluded_project_rows"]}}
            if not any(h['current'].get('summary_available', bool(h['current']['samples']))
                       for h in stats['hangars'].values()):
                raise ReportError("NO_DATA", "所选日期及机库没有对应统计数据，请核对平台统计周期")
            composite = selected_index(stats)
            stats['gaps'].extend(composite['gaps'])
            artifacts = self.cfg.state_dir / "artifacts"
            artifacts.mkdir(mode=0o700, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".report-", dir=artifacts))
            self.store.stage(identifier, "forecast")
            stats['forecast'] = self.forecast_loader(stats, self.cfg, staging)
            stats['gaps'] = [g for g in stats['gaps'] if g['code'] != 'FORECAST_UNAVAILABLE']
            stats['gaps'].extend({'code': 'FORECAST_UNAVAILABLE', 'message': message}
                                 for message in stats['forecast']['gaps'])
            stats['report_index'] = composite
            result['gaps'] = stats['gaps']
            self.store.stage(identifier, "writing")
            analyses = (self.writer.write_report(stats) if hasattr(self.writer, 'write_report') else
                        {hid: self.writer.write(hangar) for hid, hangar in stats["hangars"].items()})
            self.store.stage(identifier, "rendering")
            rendered = self.renderer(stats, analyses, composite, self.cfg, staging)
            expected = [staging / "report.docx", staging / "report.pdf"]
            if not all(p.is_file() and p.stat().st_size > 100 for p in expected):
                raise ReportError("RENDER_FAILED", "报告文件生成不完整")
            # Persist aggregates and bindings; never connection settings or raw samples.
            evidence = {"generated_at": now(), "statistics": stats, "composite": composite,
                        "writing": {k: asdict(v) for k, v in analyses.items()},
                        "render": rendered,
                        "artifact_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in expected}}
            (staging / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            for p in staging.iterdir():
                if p.is_file():
                    os.chmod(p, 0o600)
            final = artifacts / identifier
            if final.exists():
                raise ReportError("ARTIFACT_EXISTS", "报告目录已存在，拒绝覆盖")
            staging.rename(final)
            staging = None
            result = {"gaps": stats["gaps"], "summary": {
                "sample_count": sum(h["current"]["samples"] for h in stats["hangars"].values()),
                "hangar_count": len(stats["hangars"]), "report_type": request.report_type, "rules_version": stats["rules_version"],
                "writing_engine": self.cfg.writing_engine, "model": self.cfg.ollama_model if self.cfg.writing_engine == "ollama" else None, "excluded_project_rows": stats["excluded_project_rows"]}}
            self.store.finish(identifier, result=result)
        except ReportError as exc:
            self.store.finish(identifier, result=result, error=exc)
            LOG.warning("report=%s failed code=%s", identifier, exc.code)
        except Exception as exc:
            # Exception strings may contain DB credentials, model prompts or file paths.
            self.store.finish(identifier, result=result, error=ReportError("GENERATION_FAILED", "报告生成失败，请联系服务维护人员核查运行环境"))
            LOG.error("report=%s failed type=%s", identifier, type(exc).__name__)
        finally:
            audit = getattr(self.writer, 'call_audit', None)
            if audit:
                diagnostics = self.cfg.state_dir / 'diagnostics'
                diagnostics.mkdir(mode=0o700, exist_ok=True)
                # Recovery may rerun an interrupted job: retain each diagnostic attempt.
                fd, _ = tempfile.mkstemp(prefix=identifier+'-', suffix='.json', dir=diagnostics)
                with os.fdopen(fd, 'w') as handle:
                    json.dump({'calls': audit, 'advice_attempts': getattr(self.writer, 'advice_attempts', [])}, handle, ensure_ascii=False, indent=2)
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
        return True

    def run(self, once=False):
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        with worker_lock(self.cfg.state_dir):
            self.store.recover()
            # No other worker owns these transient directories under this lock.
            for path in (self.cfg.state_dir / "artifacts").glob(".report-*"):
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
            if once:
                return self.process_one()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            while not stopping:
                if not self.process_one():
                    time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="air-more 单worker报告生成服务")
    parser.add_argument("--once", action="store_true", help="处理一个排队任务后退出")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    Worker(Settings.from_env()).run(once=args.once)


if __name__ == "__main__":
    main()
