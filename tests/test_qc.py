"""质控逻辑测试：缺测 / 重复 / 时区 / 范围 / ox 标注。"""

import pandas as pd
import pytest

from airmore.ingest.csv_loader import load_csv_dir
from airmore.qc.checks import run_qc


def _row(ts, sid="S-1", pm25=30.0):
    return {
        "timestamp": ts, "sortie_id": sid, "hangar_id": "HANGAR-A",
        "lat": 31.23, "lon": 121.47, "altitude_m": 80.0,
        "pm1_0": 15.0, "pm2_5": pm25, "pm10": 45.0, "so2": 8.0,
        "no2": 24.0, "o3": 60.0, "co": 0.6, "ch4": 1.9, "ox": 0.5,
        "temperature": 15.0, "humidity": 60.0, "pressure": 1015.0,
        "wind_speed": 2.5, "wind_direction": 220.0,
    }


def _write_csv(tmp_path, rows, name="t.csv"):
    df = pd.DataFrame(rows)
    p = tmp_path / name
    df.to_csv(p, index=False)
    return p


def test_missing_and_duplicate(tmp_path):
    rows = [_row("2026-03-15T08:00:00+08:00"),
            _row("2026-03-15T08:01:00+08:00", pm25=None),
            _row("2026-03-15T08:01:00+08:00", pm25=None)]  # 重复行
    _write_csv(tmp_path, rows)
    res = load_csv_dir(tmp_path)
    qc = run_qc(res.df)
    kinds = {(r.check, r.severity) for r in qc.records}
    assert ("重复记录", "warning") in kinds
    assert ("缺测", "warning") in kinds
    # 重复行已剔除
    assert len(qc.df) == 2


def test_naive_timestamp_rejected(tmp_path):
    rows = [_row("2026-03-15T08:00:00+08:00"),
            _row("2026-03-15 08:01:00")]  # 无时区
    _write_csv(tmp_path, rows)
    res = load_csv_dir(tmp_path)
    assert any("时区" in i for i in res.issues)
    assert len(res.df) == 1
    assert str(res.df["timestamp"].dt.tz) == "UTC"


def test_range_check_sets_nan(tmp_path):
    rows = [_row("2026-03-15T08:00:00+08:00", pm25=30.0),
            _row("2026-03-15T08:01:00+08:00", pm25=99999.0)]  # 超出物理范围
    _write_csv(tmp_path, rows)
    qc = run_qc(load_csv_dir(tmp_path).df)
    assert any(r.check == "范围异常" for r in qc.records)
    assert qc.df["pm2_5"].isna().sum() == 1


def test_ox_not_computed(tmp_path):
    _write_csv(tmp_path, [_row("2026-03-15T08:00:00+08:00")])
    qc = run_qc(load_csv_dir(tmp_path).df)
    ox_records = [r for r in qc.records if r.check == "ox 字段"]
    assert len(ox_records) == 1
    assert "未参与" in ox_records[0].detail
