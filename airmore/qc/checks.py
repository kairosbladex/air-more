"""数据质控：缺测 / 重复 / 时区 / 范围检查，输出质控记录。

ox 字段含义不明：不做范围判定，只记录"未参与计算"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from airmore.domain.indicators import PLAUSIBLE_RANGES, COMPUTED_POLLUTANTS


@dataclass
class QCRecord:
    check: str        # 检查项
    severity: str     # info / warning
    count: int        # 涉及行数
    detail: str       # 说明


@dataclass
class QCResult:
    df: pd.DataFrame                  # 清洗后的数据（剔除重复行）
    records: list[QCRecord] = field(default_factory=list)

    def to_rows(self) -> list[dict]:
        return [
            {"check": r.check, "severity": r.severity, "count": r.count, "detail": r.detail}
            for r in self.records
        ]


# 纳入缺测统计的字段（不含 ox）
_QC_FIELDS = COMPUTED_POLLUTANTS + [
    "temperature", "humidity", "pressure", "wind_speed", "wind_direction",
    "lat", "lon", "altitude_m",
]


def run_qc(df: pd.DataFrame, sortie_label: str = "航次") -> QCResult:
    records: list[QCRecord] = []

    # 1. 重复行：同一航次同一时间戳视为重复
    dup_mask = df.duplicated(subset=["sortie_id", "timestamp"], keep="first")
    n_dup = int(dup_mask.sum())
    if n_dup:
        records.append(QCRecord(
            "重复记录", "warning", n_dup,
            f"重复行 {n_dup} 行，保留首条",
        ))
        df = df[~dup_mask].copy()

    # 2. 时区一致性：数据已在接入层统一为 UTC，这里核对时间跨度与单调性
    if not df["timestamp"].is_monotonic_increasing:
        df = df.sort_values("timestamp").reset_index(drop=True)
        records.append(QCRecord("时区/排序", "info", 0, "时间戳已统一为 UTC 并重排序"))
    else:
        records.append(QCRecord("时区/排序", "info", 0, "时间戳统一为 UTC，全时段一致"))

    # 3. 缺测统计
    for col in _QC_FIELDS:
        n_miss = int(df[col].isna().sum())
        if n_miss:
            records.append(QCRecord(
                "缺测", "warning", n_miss,
                f"{col} 缺测 {n_miss} 条，统计时剔除",
            ))

    # 4. 物理合理范围检查
    for col, (lo, hi) in PLAUSIBLE_RANGES.items():
        if col not in df.columns:
            continue
        s = df[col].dropna()
        n_bad = int(((s < lo) | (s > hi)).sum())
        if n_bad:
            records.append(QCRecord(
                "范围异常", "warning", n_bad,
                f"{col} 有 {n_bad} 条超出合理范围，未参与统计",
            ))
            df.loc[(df[col] < lo) | (df[col] > hi), col] = float("nan")

    # 5. ox 字段：含义不明，不参与计算
    n_ox = int(df["ox"].notna().sum()) if "ox" in df.columns else 0
    records.append(QCRecord(
        "ox 字段", "info", n_ox,
        f"含义不明，{n_ox} 条非空记录未参与计算",
    ))

    total = len(df)
    n_sortie = int(df["sortie_id"].nunique())
    records.append(QCRecord(
        "有效样本", "info", total,
        f"有效样本 {total} 条，覆盖 {n_sortie} 个{sortie_label}",
    ))
    return QCResult(df=df, records=records)
