"""Checks against the frozen public-source report, without network requests."""

import copy
from pathlib import Path

import pytest

from scripts.public_jinan_report import (CITIES, advice_text, build_view, load_sources,
                                        month_summary, validate_history)

SNAPSHOT = Path(__file__).resolve().parents[1] / "data/regional/public-jinan-20260909"


def test_public_source_crosscheck_and_summary():
    sources, histories, weather, outlook = load_sources(SNAPSHOT)
    assert len(sources) == 11
    assert len(histories) == 8
    assert all(len(rows) == 8 for rows in histories.values())
    assert histories[370100][-1]["AQI"] == "35"
    assert histories[370100][-1]["PM2_5_24h"] == "10"
    view, summaries = build_view(histories, weather, outlook, sources)
    assert view["blocks"][-2]["text"] == "四、工作建议"
    assert view["blocks"][-1]["text"] == advice_text(histories, summaries)
    assert "业务建议：" not in view["blocks"][-1]["text"]
    assert "[E" not in view["blocks"][-1]["text"]
    for code in CITIES:
        values = [int(r["AQI"]) for r in histories[code]]
        assert summaries[code]["good_days"] == sum(v <= 100 for v in values)
        assert summaries[code]["aqi_max"] == max(values)
        assert summaries[code]["aqi_min"] == min(values)


@pytest.mark.parametrize("failure", ["missing_day", "duplicate", "wrong_city", "invalid_number"])
def test_reject_wrong_or_incomplete_monitoring(failure):
    _, histories, _, _ = load_sources(SNAPSHOT)
    rows = copy.deepcopy(histories[370100])
    if failure == "missing_day":
        rows.pop(0)
    elif failure == "duplicate":
        rows.append(rows[0])
    elif failure == "wrong_city":
        rows[0]["CityCode"] = 370300
    else:
        rows[0]["CO_24h"] = "nan"
    with pytest.raises(ValueError):
        validate_history(rows, 370100)


def test_summary_uses_categories_not_mean_aqi():
    _, histories, _, _ = load_sources(SNAPSHOT)
    rows = copy.deepcopy(histories[370100][:2])
    rows[0].update(AQI="25", Quality="优")
    rows[1].update(AQI="125", Quality="轻度污染")
    s = month_summary(rows)
    assert s["good_days"] == 1
    assert s["good_percent"] == 50
