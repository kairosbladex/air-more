"""阈值筛查与持续性判定规则。

输出内部业务状态：关注 / 建议复核 / 优先核查。
与官方气象预警等级完全分开，不构成法定超标判定（阈值仅供演示）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from airmore.domain.indicators import POLLUTANTS

STATUS_WATCH = "关注"
STATUS_REVIEW = "建议复核"
STATUS_PRIORITY = "优先核查"
STATUS_ORDER = {STATUS_WATCH: 1, STATUS_REVIEW: 2, STATUS_PRIORITY: 3}


@dataclass
class ThresholdConfig:
    version: str
    disclaimer: str
    reference: str
    pollutants: dict          # field -> {threshold, unit, basis}
    persistence: dict         # watch_minutes / review_minutes / priority_minutes / peak_factor / max_gap_minutes
    internal_status_note: str

    @classmethod
    def load(cls, path: str | Path) -> "ThresholdConfig":
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            version=cfg["version"],
            disclaimer=cfg["disclaimer"],
            reference=cfg["reference"],
            pollutants=cfg["pollutants"],
            persistence=cfg["persistence"],
            internal_status_note=cfg["internal_status_note"],
        )


@dataclass
class Event:
    field: str
    sortie_id: str
    hangar_id: str
    start: pd.Timestamp
    end: pd.Timestamp
    duration_minutes: float
    peak: float
    peak_time: pd.Timestamp
    peak_lat: float
    peak_lon: float
    mean: float
    samples: int
    threshold: float
    unit: str
    basis: str
    status: str                 # 关注 / 建议复核 / 优先核查
    rule_version: str

    @property
    def label(self) -> str:
        return POLLUTANTS[self.field].label


def _classify(duration_min: float, peak: float, threshold: float, p: dict) -> str:
    if duration_min >= p["priority_minutes"] or peak >= p["peak_factor"] * threshold:
        return STATUS_PRIORITY
    if duration_min >= p["review_minutes"]:
        return STATUS_REVIEW
    return STATUS_WATCH


def screen_events(df: pd.DataFrame, cfg: ThresholdConfig) -> list[Event]:
    """对每个配置了阈值的指标，按航次扫描连续超阈值片段。

    连续定义：相邻样本时间差不超过 max_gap_minutes。
    片段时长 ≥ watch_minutes 才记为事件。
    """
    p = cfg.persistence
    max_gap = pd.Timedelta(minutes=p["max_gap_minutes"])
    events: list[Event] = []
    for field, th in cfg.pollutants.items():
        if field not in df.columns:
            continue
        threshold = float(th["threshold"])
        sub = df.dropna(subset=[field])
        for (sid, hid), g in sub.groupby(["sortie_id", "hangar_id"]):
            g = g.sort_values("timestamp")
            over = g[g[field] > threshold]
            if over.empty:
                continue
            # 切分连续片段
            grp = (over["timestamp"].diff() > max_gap).cumsum()
            for _, seg in over.groupby(grp):
                start, end = seg["timestamp"].iloc[0], seg["timestamp"].iloc[-1]
                duration = (end - start).total_seconds() / 60.0
                if duration < p["watch_minutes"]:
                    continue
                imax = seg[field].idxmax()
                peak = float(seg[field].max())
                events.append(Event(
                    field=field, sortie_id=str(sid), hangar_id=str(hid),
                    start=start, end=end,
                    duration_minutes=round(duration, 1),
                    peak=round(peak, 1),
                    peak_time=df.loc[imax, "timestamp"],
                    peak_lat=round(float(df.loc[imax, "lat"]), 5),
                    peak_lon=round(float(df.loc[imax, "lon"]), 5),
                    mean=round(float(seg[field].mean()), 1),
                    samples=int(len(seg)),
                    threshold=threshold, unit=th["unit"], basis=th["basis"],
                    status=_classify(duration, peak, threshold, p),
                    rule_version=cfg.version,
                ))
    events.sort(key=lambda e: (e.start, e.field))
    return events


def events_status_rank(events: list[Event]) -> str | None:
    """全部事件中最高内部状态。"""
    if not events:
        return None
    return max((e.status for e in events), key=lambda s: STATUS_ORDER[s])
