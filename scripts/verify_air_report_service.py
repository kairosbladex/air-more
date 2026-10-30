"""Explicit local acceptance harness. No DB writes, mock inference or deployment.

python scripts/verify_air_report_service.py synthetic --output /private/new-directory
python scripts/verify_air_report_service.py database --output /private/new-directory
Settings and credentials are read only from AIRMORE_* environment variables.
"""
import argparse
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from airmore.service.api import create_app
from airmore.service.config import Settings
from airmore.service.examples import synthetic_case
from airmore.service.repository import MySQLRepository
from airmore.service.worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["synthetic", "database"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--missing", action="store_true")
    parser.add_argument("--hangars", type=int, choices=range(1, 51), default=2)
    parser.add_argument("--start-date", default="2026-08-10")
    parser.add_argument("--end-date", default="2026-08-12")
    parser.add_argument("--report-type", choices=["DAY", "WEEK", "MONTH"], default="MONTH")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    cfg = Settings.from_env()
    cfg.state_dir = args.output.resolve()
    if args.mode == "database":
        result = MySQLRepository(cfg).audit()
    else:
        import secrets
        request, dataset, profiles = synthetic_case(args.hangars, missing=args.missing,
            start_date=args.start_date, end_date=args.end_date, report_type=args.report_type)
        cfg.measurement_profiles = profiles
        cfg.clients = {"acceptance": secrets.token_urlsafe(32)}
        class SyntheticRepository:
            def resolve_project(self, _):
                return request.project_id
            def load(self, _):
                return dataset
        client = TestClient(create_app(cfg))
        headers = {"Authorization": "Bearer " + cfg.clients["acceptance"], "Idempotency-Key": "acceptance", "X-Requester-Id": request.requester_id}
        response = client.post("/api/v1/air-reports", json={"siteIds":request.hangar_ids,"reportType":request.report_type,"startDate":str(request.start_date),"endDate":str(request.end_date)}, headers=headers)
        assert response.status_code == 202
        job_id = response.json()["report_id"]
        Worker(cfg, repository=SyntheticRepository()).run(once=True)
        headers["X-Requester-Id"] = request.requester_id
        status = client.get("/api/v1/air-reports/" + job_id, headers=headers)
        result = status.json()
        if result["status"] != "succeeded_with_gaps":
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        for fmt, url in result["files"].items():
            response = client.get(url, headers=headers)
            assert response.status_code == 200
            actual = cfg.state_dir / "artifacts" / job_id / ("report." + fmt)
            assert hashlib.sha256(response.content).digest() == hashlib.sha256(actual.read_bytes()).digest()
        result["verification"] = {"http_adapter": "FastAPI TestClient", "inference": "real Ollama", "downloads": "sha256 matched"}
    (args.output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
