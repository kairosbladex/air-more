"""Environment-only secrets and explicit measurement configuration."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    state_dir: Path = Path("var-airmore")
    clients: dict[str, str] = field(default_factory=dict, repr=False)
    db_host: str = ""
    db_port: int = 3306
    db_name: str = ""
    db_user: str = field(default="", repr=False)
    db_password: str = field(default="", repr=False)
    query_timeout_ms: int = 10000
    max_rows: int = 500000
    max_days: int = 366
    max_hangars: int = 50
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    model_timeout: float = 120
    atmospheric_load_types: tuple[str, ...] = ("1003",)
    writing_engine: str = "ollama"
    company: str = "北京卓翼航空装备集团有限公司"
    soffice: str = "soffice"
    # Server-owned, explicit attestations; no web request can enable standard AQI.
    measurement_profiles: dict = field(default_factory=dict)
    indicators_projects: dict = field(default_factory=dict, repr=False)
    forecast_enabled: bool = False

    def __post_init__(self):
        if not self.atmospheric_load_types or any(not isinstance(v, str) or not v for v in self.atmospheric_load_types):
            raise ValueError("atmospheric_load_types must contain explicit type codes")
        if self.writing_engine not in {"ollama", "template"}:
            raise ValueError("writing_engine must be ollama or template")
        self.state_dir = Path(self.state_dir).resolve()
        if any(not isinstance(k, str) or not k or not isinstance(v, str) or len(v) < 24 or v.startswith("REPLACE_")
               for k, v in self.clients.items()):
            raise ValueError("服务凭证必须为非空调用方名称及至少 24 字符的密钥")
        if len(set(self.clients.values())) != len(self.clients):
            raise ValueError("不同调用方不得共用密钥")
        for n in (self.max_days, self.max_hangars, self.max_rows, self.query_timeout_ms, self.model_timeout):
            if n <= 0:
                raise ValueError("服务限制必须大于零")

    @classmethod
    def from_env(cls):
        def val(key, default=""):
            return os.environ.get("AIRMORE_" + key, default)
        path = val("MEASUREMENT_PROFILES")
        profiles = json.loads(Path(path).read_text("utf-8")) if path else {}
        indicators_path = val("INDICATORS_PROJECTS")
        indicators = json.loads(Path(indicators_path).read_text("utf-8")) if indicators_path else {}
        return cls(
            state_dir=Path(val("STATE_DIR", "var-airmore")),
            clients=json.loads(val("CLIENTS_JSON", "{}")),
            db_host=val("DB_HOST"), db_port=int(val("DB_PORT", "3306")),
            db_name=val("DB_NAME"), db_user=val("DB_USER"), db_password=val("DB_PASSWORD"),
            query_timeout_ms=int(val("QUERY_TIMEOUT_MS", "10000")),
            max_rows=int(val("MAX_ROWS", "500000")), max_days=int(val("MAX_DAYS", "366")),
            max_hangars=int(val("MAX_HANGARS", "50")),
            ollama_url=val("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=val("OLLAMA_MODEL", "qwen3.5:9b"),
            model_timeout=float(val("MODEL_TIMEOUT", "120")),
            atmospheric_load_types=tuple(json.loads(val("ATMOSPHERIC_LOAD_TYPES", '["1003"]'))),
            writing_engine=val("WRITING_ENGINE", "ollama"),
            company=val("COMPANY", "北京卓翼航空装备集团有限公司"),
            soffice=val("SOFFICE", "soffice"), measurement_profiles=profiles,
            indicators_projects=indicators, forecast_enabled=val('FORECAST_ENABLED', '0') == '1')
