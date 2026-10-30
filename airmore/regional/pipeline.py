"""Generate one bulletin as an atomic, non-overwriting artifact directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from .bundle import TZ, load_bundle, require
from .render import convert_pdf, preflight, render_docx, verify_docx
from .report import build_view, validate_view


def generate(bundle_path: str | Path, out_dir: str | Path) -> dict:
    bundle = load_bundle(bundle_path)
    view = build_view(bundle)
    validate_view(bundle, view)
    runtime = preflight()
    m = bundle.manifest
    region = re.sub(r"[^\w\-]", "_", m["region"]["name"])
    stem = f"{region}_{m['report_date']}_第{m['issue']}期"
    require(len(stem.encode("utf-8")) <= 200, "report filename too long")
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / stem
    require(not final.exists(), f"report already exists: {final}")
    lock = root / f".{stem}.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"report generation already in progress: {stem}") from exc
    os.close(fd)
    staging = None
    try:
        require(not final.exists(), f"report already exists: {final}")
        staging = Path(tempfile.mkdtemp(prefix=".regional-", dir=root))
        docx = staging / f"{stem}.docx"
        media = render_docx(bundle, view, docx, runtime)
        verify_docx(docx, view, media)
        pdf = convert_pdf(docx, runtime)
        evidence = bundle.evidence()
        evidence.update(status="complete_with_gaps" if view["missing"] else "complete",
                        generated_at=datetime.now(TZ).isoformat(), view=view,
                        writing_engine="deterministic-template",
                        runtime={k: v for k, v in runtime.items() if k != "env"},
                        artifact_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (docx, pdf)},
                        media_sha256={k: hashlib.sha256(v).hexdigest() for k, v in media.items()})
        evidence_path = staging / f"{stem}.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        require(json.loads(evidence_path.read_text("utf-8")) == evidence, "evidence JSON readback failed")
        require(not final.exists(), f"report already exists: {final}")
        staging.rename(final)
        staging = None
        return {"directory": str(final), "status": evidence["status"],
                **{ext: str(final / f"{stem}.{ext}") for ext in ("docx", "pdf", "json")}}
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink(missing_ok=True)
