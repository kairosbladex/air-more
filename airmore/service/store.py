"""SQLite job queue with atomic claims and caller-scoped idempotency."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import ReportError, ReportRequest


def now():
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "jobs.sqlite3"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, caller TEXT NOT NULL, requester TEXT NOT NULL,
                idem TEXT NOT NULL, request_hash TEXT NOT NULL, request TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, result TEXT, error TEXT,
                UNIQUE(caller, idem))""")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(self, caller, idem, request: ReportRequest):
        payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM jobs WHERE caller=? AND idem=?", (caller, idem)).fetchone()
            if old:
                if old["request_hash"] != digest:
                    raise ReportError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
                return dict(old)
            identifier, stamp = uuid.uuid4().hex, now()
            conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (identifier, caller, request.requester_id, idem, digest, payload,
                          "queued", "queued", stamp, stamp, None, None))
            return dict(conn.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone())

    def owned(self, identifier, caller, requester):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=? AND caller=? AND requester=?",
                               (identifier, caller, requester)).fetchone()
        if row is None:
            raise ReportError("NOT_FOUND", "报告不存在或无权访问")
        return dict(row)

    def recover(self):
        error = json.dumps({"code": "WORKER_INTERRUPTED", "message": "生成进程中断，请使用新幂等键重新提交"}, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status='failed', error=?, updated_at=? WHERE status='running'", (error, now()))

    def claim(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute("UPDATE jobs SET status='running',stage='querying',updated_at=? WHERE id=?", (now(), row["id"]))
            return dict(row)

    def stage(self, identifier, stage):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND status='running'", (stage, now(), identifier))

    def finish(self, identifier, result=None, error: ReportError | None = None):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status=?,stage=CASE WHEN ? IS NULL THEN 'complete' ELSE stage END,"
                         "result=?,error=?,updated_at=? WHERE id=? AND status='running'",
                         ("failed" if error else ("succeeded_with_gaps" if result["gaps"] else "succeeded"),
                          error.code if error else None,
                          json.dumps(result, ensure_ascii=False) if result else None,
                          json.dumps({"code": error.code, "message": error.message}, ensure_ascii=False) if error else None,
                          now(), identifier))

    @staticmethod
    def public(row):
        result = json.loads(row["result"]) if row["result"] else {}
        return {"report_id": row["id"], "status": row["status"], "stage": row["stage"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "gaps": result.get("gaps", []), "summary": result.get("summary", {}),
                "error": json.loads(row["error"]) if row["error"] else None,
                "files": {fmt: f"/api/v1/air-reports/{row['id']}/files/{fmt}"
                          for fmt in ("docx", "pdf") if row["status"].startswith("succeeded")}}
