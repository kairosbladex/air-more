"""统计聚合测试：航次等权平均避免高频航次支配。"""

import pandas as pd
import pytest

from airmore.stats.aggregate import (daily_summary, hangar_summary,
                                     sortie_stats, window_series)


def _df(spec: dict[str, tuple[int, float]]) -> pd.DataFrame:
    """spec: sortie_id -> (样本数, pm2_5 恒定值)"""
    rows = []
    for sid, (n, v) in spec.items():
        ts = pd.date_range("2026-03-15 08:00", periods=n, freq="1min", tz="UTC")
        for t in ts:
            rows.append({"timestamp": t, "sortie_id": sid,
                         "hangar_id": "H-" + sid[-1], "lat": 31.23, "lon": 121.47,
                         "altitude_m": 80.0, "pm2_5": v, "pm10": v * 1.3,
                         "temperature": 15.0, "humidity": 60.0,
                         "pressure": 1015.0, "wind_speed": 2.5})
    return pd.DataFrame(rows)


def test_sortie_weighting_avoids_high_freq_domination():
    # 高频航次 100 个样本值=10，低频航次 10 个样本值=100
    df = _df({"S-1": (100, 10.0), "S-2": (10, 100.0)})
    s = sortie_stats(df, ["pm2_5"])
    d = daily_summary(s, ["pm2_5"])
    # 按样本加权会接近 10（高频支配）；航次等权应为 55
    assert d["fields"]["pm2_5"]["mean"] == pytest.approx(55.0)
    # 极值不受平均方式影响
    assert d["fields"]["pm2_5"]["max"] == pytest.approx(100.0)
    assert d["fields"]["pm2_5"]["max_sortie"] == "S-2"


def test_sortie_stats_basic():
    df = _df({"S-1": (10, 40.0)})
    s = sortie_stats(df, ["pm2_5"])
    assert s["S-1"]["samples"] == 10
    assert s["S-1"]["fields"]["pm2_5"]["mean"] == pytest.approx(40.0)
    assert s["S-1"]["fields"]["pm2_5"]["count"] == 10


def test_hangar_summary():
    df = _df({"S-1": (10, 20.0), "S-2": (10, 60.0)})
    s = sortie_stats(df, ["pm2_5"])
    h = hangar_summary(s, "pm2_5")
    assert set(h) == {"H-1", "H-2"}
    assert h["H-1"]["mean"] == pytest.approx(20.0)
    assert h["H-2"]["max"] == pytest.approx(60.0)


def test_window_series():
    df = _df({"S-1": (30, 50.0)})
    w = window_series(df, "pm2_5", "10min")
    assert len(w) == 3
    assert w["mean"].mean() == pytest.approx(50.0)
