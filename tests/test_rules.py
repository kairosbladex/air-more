"""规则触发测试：阈值筛查、持续性判定、内部状态分级。"""

import pandas as pd
import pytest

from airmore.rules.screening import (STATUS_PRIORITY, STATUS_REVIEW,
                                     STATUS_WATCH, ThresholdConfig,
                                     events_status_rank, screen_events)


@pytest.fixture()
def cfg() -> ThresholdConfig:
    return ThresholdConfig(
        version="test-1",
        disclaimer="仅供演示，非正式标准判定",
        reference="测试",
        pollutants={
            "pm2_5": {"threshold": 75.0, "unit": "μg/m³", "basis": "测试"},
            "ch4": {"threshold": 2.0, "unit": "ppm", "basis": "测试"},
        },
        persistence={"watch_minutes": 5, "review_minutes": 15,
                     "priority_minutes": 25, "peak_factor": 2.0,
                     "max_gap_minutes": 3},
        internal_status_note="内部状态与官方预警无关",
    )


def _series(values_times, sid="S-1", hid="HANGAR-A", field_vals=None):
    rows = []
    for t, v in values_times:
        row = {"timestamp": pd.Timestamp(t, tz="UTC"), "sortie_id": sid,
               "hangar_id": hid, "lat": 31.23, "lon": 121.47,
               "pm2_5": v, "ch4": 1.9}
        if field_vals:
            row.update(field_vals(t, v))
        rows.append(row)
    return pd.DataFrame(rows)


def test_no_event_below_threshold(cfg):
    df = _series([("2026-03-15 08:00", 40.0), ("2026-03-15 08:01", 50.0)])
    assert screen_events(df, cfg) == []


def test_short_exceedance_not_event(cfg):
    # 只持续 3 分钟，低于 watch_minutes=5
    df = _series([(f"2026-03-15 08:0{i}", 80.0) for i in range(4)])
    assert screen_events(df, cfg) == []


def test_watch_event(cfg):
    # 08:00–08:09 持续 9 分钟 ≥ watch 但 < review
    df = _series([(f"2026-03-15 08:{i:02d}", 80.0) for i in range(10)])
    events = screen_events(df, cfg)
    assert len(events) == 1
    e = events[0]
    assert e.field == "pm2_5"
    assert e.status == STATUS_WATCH
    assert e.duration_minutes == pytest.approx(9.0)
    assert e.peak == pytest.approx(80.0)


def test_review_event(cfg):
    # 持续 20 分钟 → 建议复核
    times = pd.date_range("2026-03-15 08:00", periods=21, freq="1min")
    df = _series([(t, 90.0) for t in times])
    (e,) = screen_events(df, cfg)
    assert e.status == STATUS_REVIEW
    assert e.duration_minutes == pytest.approx(20.0)


def test_priority_by_peak(cfg):
    # 峰值 ≥ 2 倍阈值即优先核查（即使时长不足 priority_minutes）
    times = pd.date_range("2026-03-15 08:00", periods=10, freq="1min")
    df = _series([(t, 80.0) for t in times[:5]]
                 + [(times[5], 160.0)]
                 + [(t, 80.0) for t in times[6:]])
    (e,) = screen_events(df, cfg)
    assert e.status == STATUS_PRIORITY
    assert e.peak == pytest.approx(160.0)


def test_gap_splits_segments(cfg):
    # 间隔超过 max_gap_minutes 的超过阈值片段不合并
    seg1 = [(f"2026-03-15 08:{i:02d}", 80.0) for i in range(7)]
    seg2 = [(f"2026-03-15 09:{i:02d}", 80.0) for i in range(7)]
    df = _series(seg1 + seg2)
    events = screen_events(df, cfg)
    assert len(events) == 2


def test_ch4_event(cfg):
    times = pd.date_range("2026-03-15 08:00", periods=8, freq="1min")
    df = _series([(t, 30.0) for t in times],
                 field_vals=lambda t, v: {"ch4": 2.3})
    events = screen_events(df, cfg)
    ch4_events = [e for e in events if e.field == "ch4"]
    assert len(ch4_events) == 1
    assert ch4_events[0].unit == "ppm"


def test_events_status_rank(cfg):
    times = pd.date_range("2026-03-15 08:00", periods=30, freq="1min")
    df = _series([(t, 90.0) for t in times])
    events = screen_events(df, cfg)
    assert events_status_rank(events) == STATUS_PRIORITY
    assert events_status_rank([]) is None
