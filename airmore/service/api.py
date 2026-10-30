"""Service-to-service API. Run uvicorn airmore.service.api:create_app --factory."""
import hmac
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .models import CreateReportRequest, Identifier, ReportError, ReportRequest, ReportStatus
from .store import JobStore


def create_app(settings: Settings | None = None):
    cfg = settings or Settings.from_env()
    if not cfg.clients:
        raise ValueError("必须配置 AIRMORE_CLIENTS_JSON，服务拒绝匿名启动")
    store = JobStore(cfg.state_dir)
    app = FastAPI(title="air-more 空气质量报告服务", version="1.1.0")
    app.state.store, app.state.settings = store, cfg
    bearer = HTTPBearer(auto_error=False)

    def caller(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials and credentials.scheme.lower() == "bearer":
            for name, token in cfg.clients.items():
                if hmac.compare_digest(credentials.credentials.encode(), token.encode()):
                    return name
        raise HTTPException(401, "服务凭证无效", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(ReportError)
    async def report_error(_, exc):
        return JSONResponse(status_code=404 if exc.code == "NOT_FOUND" else 409,
                            content={"code": exc.code, "message": exc.message})

    @app.post("/api/v1/air-reports", status_code=202, response_model=ReportStatus)
    def create(request: CreateReportRequest, identity: Annotated[str, Depends(caller)],
               x_requester_id: Annotated[Identifier, Header()],
               idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]):
        if request.days > cfg.max_days or len(request.hangar_ids) > cfg.max_hangars:
            raise HTTPException(422, f"最多 {cfg.max_days} 天、{cfg.max_hangars} 个机库")
        internal = ReportRequest(**request.model_dump(), requester_id=x_requester_id)
        return store.public(store.create(identity, idempotency_key, internal))

    @app.get("/api/v1/air-reports/{report_id}", response_model=ReportStatus)
    def status(report_id: str, identity: Annotated[str, Depends(caller)],
               x_requester_id: Annotated[str, Header(min_length=1, max_length=64)]):
        return store.public(store.owned(report_id, identity, x_requester_id))

    @app.get("/api/v1/air-reports/{report_id}/files/{format}")
    def download(report_id: str, format: Literal["docx", "pdf"], identity: Annotated[str, Depends(caller)],
                 x_requester_id: Annotated[str, Header(min_length=1, max_length=64)]):
        job = store.owned(report_id, identity, x_requester_id)
        if not job["status"].startswith("succeeded"):
            raise ReportError("NOT_READY", "报告尚未完成")
        root = (cfg.state_dir / "artifacts").resolve()
        path = root / job["id"] / f"report.{format}"
        if not path.is_file() or not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ReportError("NOT_FOUND", "报告文件不存在")
        return FileResponse(path, filename=f"air-report-{job['id']}.{format}",
                            media_type="application/pdf" if format == "pdf" else
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Cache-Control": "private, no-store"})

    return app
