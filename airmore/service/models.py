"""Public request contract and internal, serializable data boundaries."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TZ = ZoneInfo("Asia/Shanghai")
METRICS = ("so2", "no2", "co", "o3", "pm10", "pm2_5")
LABELS = dict(zip(METRICS, ("SO₂", "NO₂", "CO", "O₃", "PM₁₀", "PM₂.₅")))
UNITS = {m: "mg/m³" if m == "co" else "μg/m³" for m in METRICS}


def survey_value_text(value, unit, precision=None):
    """Use the same precision in tables and model-bound survey facts."""
    digits = precision if precision is not None else (4 if unit in ('ppm', 'ppb') else 2)
    return format(value, f'.{digits}f') if value is not None else '/'


Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]


class ReportWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hangar_ids: list[Identifier] = Field(min_length=1)
    start_date: date
    end_date: date
    report_type: Literal["DAY", "WEEK", "MONTH"] = "MONTH"

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        if isinstance(value, datetime):
            raise ValueError("必须是 YYYY-MM-DD 日期")
        if isinstance(value, str):
            if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
                raise ValueError("必须是 YYYY-MM-DD 日期")
        elif not isinstance(value, date):
            raise ValueError("必须是 YYYY-MM-DD 日期")
        return value

    @field_validator("hangar_ids")
    @classmethod
    def normalize_hangars(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("机库不能重复")
        return sorted(value)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.start_date > self.end_date:
            raise ValueError("起始日期不能晚于结束日期")
        if self.start_date.year < 1901 or self.end_date.year > 9998:
            raise ValueError("日期超出支持范围")
        return self

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def comparison(self) -> tuple[date, date]:
        previous_end = self.start_date - timedelta(days=1)
        if (self.start_date.day == 1 and self.start_date.year == self.end_date.year
                and self.start_date.month == self.end_date.month
                and self.end_date.day == calendar.monthrange(self.end_date.year, self.end_date.month)[1]):
            return previous_end.replace(day=1), previous_end
        return self.start_date - timedelta(days=self.days), previous_end

    def query_bounds(self) -> tuple[datetime, datetime]:
        return (datetime.combine(self.comparison()[0], time()),
                datetime.combine(self.end_date + timedelta(days=1), time()))


class CreateReportRequest(ReportWindow):
    """Business backend JSON contract; identity is supplied through headers."""
    hangar_ids: list[Identifier] = Field(alias="siteIds", min_length=1)
    report_type: Literal["DAY", "WEEK", "MONTH"] = Field(alias="reportType")
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")


class ReportRequest(ReportWindow):
    """Persisted worker request; old queued requests default to MONTH."""
    project_id: Identifier | None = None
    requester_id: Identifier
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def aware_cutoff(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("取数截止时间必须包含时区")
        return value

    def comparison(self):
        # Calendar-aligned month-to-date and week-to-date comparisons.
        if self.report_type == "MONTH" and self.start_date.day == 1 and self.start_date.month == self.end_date.month and self.start_date.year == self.end_date.year:
            last = self.start_date - timedelta(days=1)
            end_day = last.day if self.end_date.day == calendar.monthrange(self.end_date.year, self.end_date.month)[1] else min(self.end_date.day, last.day)
            return last.replace(day=1), last.replace(day=end_day)
        if self.report_type == "WEEK" and self.start_date.weekday() == 0 and self.days <= 7:
            return self.start_date - timedelta(days=7), self.end_date - timedelta(days=7)
        return super().comparison()

    def comparison_bounds(self):
        a, b = self.comparison()
        end = datetime.combine(b + timedelta(days=1), time())
        if self.as_of is not None:
            cutoff = self.as_of.astimezone(TZ).replace(tzinfo=None)
            if cutoff.date() <= self.end_date:
                elapsed = max(timedelta(), cutoff - datetime.combine(self.start_date, time()))
                end = min(end, datetime.combine(a, time()) + elapsed)
        return datetime.combine(a, time()), end

    def query_bounds(self):
        start, end = super().query_bounds()
        start = min(start, datetime.combine(self.start_date - timedelta(days=30), time()))
        if self.as_of is not None:
            end = min(end, self.as_of.astimezone(TZ).replace(tzinfo=None))
        return start, end


class ReportError(Exception):
    """Only public, fixed messages are exposed; never raw backend exceptions."""
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass
class Dataset:
    samples: list[dict]
    hangars: dict[str, str]
    unit_configs: list[dict] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)
    data_kind: str = "business_database"
    project_indicators: list[dict] | None = None
    provenance: dict = field(default_factory=dict)
    trend: dict | None = None
    indicator_catalog: dict | None = None


def dates(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


class PublicError(BaseModel):
    code: str
    message: str


class ReportStatus(BaseModel):
    report_id: str
    status: Literal["queued", "running", "succeeded", "succeeded_with_gaps", "failed"]
    stage: str
    created_at: str
    updated_at: str
    gaps: list[dict]
    summary: dict
    error: PublicError | None
    files: dict[str, str]
