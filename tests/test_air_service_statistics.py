from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from airmore.service.aqi import DAILY, HOURLY, LEVELS, calculate, iaqi, ozone_daily, rounded_concentration
from airmore.service.composite import UnconfiguredComposite
from airmore.service.examples import synthetic_case
from airmore.service.models import Dataset, METRICS, ReportRequest
from airmore.service.statistics import build_statistics, configured_units, measurement


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("period,bounds", [("day", DAILY), ("hour", HOURLY)])
def test_all_breakpoints(metric, period, bounds):
    for concentration, expected in zip(bounds[metric], LEVELS):
        assert iaqi(metric, concentration, period) == expected


def test_new_2026_breakpoints_rounding_caps_and_partial():
    assert iaqi("pm2_5", 60) == 100
    assert iaqi("pm10", 120) == 100
    assert iaqi("pm2_5", 36) == 52
    assert str(rounded_concentration("co", "0.25")) == "0.2"
    assert str(rounded_concentration("no2", "22.5")) == "22"
    assert iaqi("o3", 900) == 300
    assert iaqi("so2", 900, "hour") == 200
    assert iaqi("pm10", 9999) == 500
    assert calculate({"pm2_5": 60})["aqi"] is None
    result = calculate({"pm2_5": 115, "pm10": 250})
    assert result["aqi"] == 150 and result["partial"]
    assert set(result["primary_pollutants"]) == {"pm10", "pm2_5"}
    full = calculate({m: 0 for m in METRICS})
    assert full["primary_pollutants"] == [] and full["aqi"] == 1
    for value in (float("nan"), float("inf"), -1):
        with pytest.raises(ValueError):
            iaqi("co", value)


def test_ozone_windows_completeness_and_exceedance_exception():
    assert ozone_daily({h: 120 for h in range(24)}) == (120, 17)
    assert ozone_daily({h: 120 for h in range(6)}) == (None, 1)
    assert ozone_daily({h: 170 for h in range(6)}) == (170, 1)
    assert ozone_daily({h: 160 for h in range(6)}) == (None, 1)
    assert ozone_daily({h: 900 for h in range(5)}) == (None, 0)
    # No previous-day data may supply one of the day's 08:00..24:00 windows.
    assert ozone_daily({h: 170 for h in range(-7, 1)}) == (None, 0)


@pytest.mark.parametrize("start,end,expected", [
    ("2026-03-01", "2026-03-31", ("2026-02-01", "2026-02-28")),
    ("2024-03-01", "2024-03-31", ("2024-02-01", "2024-02-29")),
    ("2026-01-01", "2026-01-31", ("2025-12-01", "2025-12-31")),
    ("2026-08-10", "2026-08-25", ("2026-07-25", "2026-08-09")),
])
def test_comparison_boundaries(start, end, expected):
    request = ReportRequest(project_id="p", hangar_ids=["h"], requester_id="u", start_date=start, end_date=end)
    assert tuple(d.isoformat() for d in request.comparison()) == expected
    assert request.query_bounds()[1].date() == date.fromisoformat(end) + timedelta(days=1)


@pytest.fixture(scope="module")
def synthetic():
    return synthetic_case(count=1)


def test_fixed_point_days_and_no_composite_default(synthetic):
    request, data, profiles = synthetic
    stats = build_statistics(request, data, profiles)
    h = stats["hangars"]["demo-1"]
    assert h["current"]["valid_aqi_days"] == 3
    result = h["days"]["2026-08-12"]["standard"]
    assert result["valid_hours"]["co"] == 24 and result["ozone_valid_windows"] == 17
    assert result["concentrations"]["o3"] == 138
    assert not UnconfiguredComposite().evaluate(stats)["available"]


def test_sparse_and_mobile_samples_cannot_fill_daily_aqi():
    request, data, profiles = synthetic_case(count=1, missing=True)
    h = build_statistics(request, data, profiles)["hangars"]["demo-1"]
    assert h["current"]["valid_aqi_days"] == 0
    assert h["current"]["survey"]["pm10"]["value"] is not None
    profiles[next(iter(profiles))]["mode"] = "drone"
    h = build_statistics(request, data, profiles)["hangars"]["demo-1"]
    assert not h["days"]["2026-08-12"]["standard"]["fixed_point_eligible"]


def test_movement_and_multiple_devices_fail_closed(synthetic):
    request, source, profiles = synthetic
    for modification in ({"lon": 1}, {"load_sn": "different"}):
        data = deepcopy(source)
        data.samples[-1].update(modification)
        result = build_statistics(request, data, profiles)["hangars"]["demo-1"]["days"]["2026-08-12"]["standard"]
        assert result["aqi"] is None and not result["fixed_point_eligible"]


def test_time_filter_dedup_conflict_zero_and_unknown_units():
    request, data, profiles = synthetic_case(count=1, missing=True)
    row = deepcopy(data.samples[-1])
    row["pm10"] = 0
    data.samples = [row, deepcopy(row)]
    stats = build_statistics(request, data, profiles)
    assert stats["quality"]["duplicate_rows"] == 1
    assert stats["hangars"]["demo-1"]["current"]["survey"]["pm10"]["value"] == 0
    data.samples[-1]["pm10"] = 100
    stats = build_statistics(request, data, profiles)
    assert stats["hangars"]["demo-1"]["current"]["survey"]["pm10"]["value"] is None
    assert stats["quality"]["conflicting_pm10"] == 1
    outside = deepcopy(row)
    outside["time"] = datetime(2026, 8, 13)
    data.samples = [row, outside]
    data.samples[0]["create_time"] = datetime(2030, 1, 1)
    stats = build_statistics(request, data, profiles)
    assert stats["hangars"]["demo-1"]["current"]["samples"] == 1
    assert stats["quality"]["outside_query_window"] == 1
    stats = build_statistics(request, data, {})
    assert stats["hangars"]["demo-1"]["current"]["survey"]["co"]["value"] is None


def test_unit_conversions_and_state_gate():
    row = {"co": 1, "load_type": "type"}
    profile = {"basis": "confirmed", "units": {"co": {"unit": "ppm", "confirmed": True,
               "state": "reference_298.15K_101.325kPa"}}}
    value, unit, mass, reason = measurement(row, "co", profile, {})
    assert value == pytest.approx(1.1449, abs=.001) and unit == "mg/m³" and mass == value
    assert measurement(row, "co", profile, {("type", "co"): "μg/m³"})[3] == "unit_conflict"
    profile["units"]["co"]["state"] = "unknown"
    value, unit, mass, reason = measurement(row, "co", profile, {})
    assert value == 1 and unit == "ppm" and mass is None
    assert reason == "measurement_state_unconfirmed"
    assert configured_units([{"index_name": "co", "index_unit": "invalid"}]).get(("type", "co")) is None


def test_all_empty_is_explicit(synthetic):
    request, _, profiles = synthetic
    stats = build_statistics(request, Dataset([], {"demo-1": "测试机库"}), profiles)
    assert stats["hangars"]["demo-1"]["current"]["samples"] == 0
    assert "NO_DATA" in {g["code"] for g in stats["gaps"]}


@pytest.mark.parametrize('unit', ['ppm', 'ppb'])
def test_native_gas_survey_never_supplies_unconfirmed_aqi_mass(unit):
    row = {'load_type': 'sensor', 'so2': .025, 'pm10': 20}
    value, displayed, mass, reason = measurement(row, 'so2', {}, {('sensor', 'so2'): unit})
    assert (value, displayed, mass, reason) == (.025, unit, None, 'measurement_state_unconfirmed')
    assert measurement(row, 'pm10', {}, {('sensor', 'pm10'): unit})[3] == 'unit_incompatible'


def test_conflicting_configuration_cannot_be_overridden():
    import json
    from airmore.service.statistics import clean_number
    records = [{"index_name": "co", "index_unit": json.dumps([
        {"productNum": "type", "defaultUnit": "ppm"},
        {"productNum": "type", "defaultUnit": "mg/m³"}])}]
    profile = {"basis": "verified", "units": {"co": {"unit": "ppm", "confirmed": True}}}
    assert measurement({"load_type": "type", "co": 0}, "co", profile, configured_units(records))[3] == "unit_conflict"
    assert clean_number(True) is None and clean_number(0) == 0


def test_hour_and_day_coverage_thresholds(synthetic):
    request, original, profiles = synthetic
    for minutes, hours, expected_hours in [(44, 24, 0), (45, 19, 19), (45, 20, 20)]:
        data = deepcopy(original)
        data.samples = [r for r in data.samples if r['time'].minute < minutes and r['time'].hour < hours]
        result = build_statistics(request, data, profiles)['hangars']['demo-1']['days']['2026-08-12']['standard']
        assert result['valid_hours']['pm10'] == expected_hours
        assert (result['concentrations']['pm10'] is not None) == (expected_hours >= 20)


def test_invalid_ozone_class_invalidates_whole_day(synthetic):
    request, data, source_profiles = synthetic
    profiles = deepcopy(source_profiles)
    for profile in profiles.values():
        profile['ozone_8h_limit'] = 999
    result = build_statistics(request, data, profiles)['hangars']['demo-1']['days']['2026-08-12']['standard']
    assert not result['fixed_point_eligible']
    assert all(v is None for v in result['concentrations'].values())
