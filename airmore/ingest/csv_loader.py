"""CSV 数据接入与 schema 校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    "timestamp", "sortie_id", "hangar_id", "lat", "lon", "altitude_m",
    "pm1_0", "pm2_5", "pm10", "so2", "no2", "o3", "co", "ch4", "ox",
    "temperature", "humidity", "pressure", "wind_speed", "wind_direction",
]

NUMERIC_COLUMNS = [
    "lat", "lon", "altitude_m",
    "pm1_0", "pm2_5", "pm10", "so2", "no2", "o3", "co", "ch4", "ox",
    "temperature", "humidity", "pressure", "wind_speed", "wind_direction",
]


@dataclass
class IngestResult:
    df: pd.DataFrame
    issues: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def load_csv_dir(data_dir: str | Path) -> IngestResult:
    """加载目录下全部 CSV，校验 schema，解析带时区时间戳。"""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"数据目录不存在: {data_dir}")
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"数据目录中没有 CSV 文件: {data_dir}")

    issues: list[str] = []
    frames: list[pd.DataFrame] = []
    for f in files:
        df = pd.read_csv(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{f.name} 缺少必需列: {missing}")
        df = df[REQUIRED_COLUMNS].copy()
        df["source_file"] = f.name
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    # 时间戳解析：要求带时区（ISO8601）
    raw_ts = df["timestamp"].astype(str)
    naive = raw_ts[~raw_ts.str.contains(r"[+-]\d{2}:?\d{2}|Z$", regex=True)]
    if len(naive) > 0:
        issues.append(f"{len(naive)} 行时间戳缺少时区信息，已剔除")
        df = df[raw_ts.str.contains(r"[+-]\d{2}:?\d{2}|Z$", regex=True)].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    bad_ts = int(df["timestamp"].isna().sum())
    if bad_ts:
        issues.append(f"{bad_ts} 行时间戳无法解析，已剔除")
        df = df.dropna(subset=["timestamp"])

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["sortie_id"] = df["sortie_id"].astype(str)
    df["hangar_id"] = df["hangar_id"].astype(str)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return IngestResult(df=df, issues=issues, files=[f.name for f in files])
