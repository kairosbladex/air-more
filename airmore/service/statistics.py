"""Quality control and explicitly separated survey statistics / eligible daily AQI."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from statistics import mean

from .aqi import VERSION, calculate, ozone_daily, rounded_concentration
from .models import Dataset, METRICS, ReportRequest, TZ, UNITS, dates

MW = {"so2": 64.07, "no2": 46.01, "co": 28.01, "o3": 48.0}
ALIASES = {"ug/m3": "μg/m³", "µg/m³": "μg/m³", "μg/m3": "μg/m³",
           "mg/m3": "mg/m³", "ppm": "ppm", "ppb": "ppb", "μg/m³": "μg/m³", "mg/m³": "mg/m³"}


def clean_number(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def profile_key(row):
    return f"{row['project_id']}:{row['load_type']}:{row['load_sn']}"


def configured_units(records):
    found = defaultdict(set)
    for record in records:
        metric = record.get("index_name")
        if metric not in METRICS:
            continue
        try:
            entries = json.loads(record["index_unit"])
            if not isinstance(entries, list):
                raise ValueError("unit config must be an array")
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("unit config entry must be an object")
                product = item.get("productNum")
                if product is not None and (not isinstance(product, str) or not product):
                    raise ValueError("invalid product scope")
                named = ALIASES.get(item.get('defaultUnit'))
                code = item.get('defaultUnitCode')
                matches = [ALIASES.get(r.get('value')) for r in record.get('unit_dictionary', []) if r.get('code') == code] if code else []
                coded = matches[0] if len(matches) == 1 else None
                # A supplied dictionary is authoritative. Missing/duplicate
                # codes or conflicting names cannot be hidden by an override.
                if ('unit_dictionary' in record and code and
                        (coded is None or (item.get('defaultUnit') and named != coded))):
                    found[(product if product is not None else '__default__', metric)].update({None, '__conflict__'})
                    continue
                found[(product if product is not None else "__default__", metric)].add(coded or named)
        except (ValueError, TypeError, KeyError):
            # Malformed config invalidates the metric, not just the bad row.
            found[("*", metric)].add(None)
    return {k: next(iter(v)) if len(v) == 1 and ("*", k[1]) not in found else "__conflict__"
            for k, v in found.items()}


def measurement(row, metric, profile, units, validity=None):
    """Return (value, display unit, eligible mass value, missing reason)."""
    if validity is not None and not validity['valid']:
        return None, None, None, validity['reason']
    raw = clean_number(row.get(metric))
    if raw is None:
        return None, None, None, "missing_or_invalid_value"
    spec = profile.get("units", {}).get(metric, {})
    source_unit = units.get((row.get('unit_product_num', row["load_type"]), metric), units.get(("__default__", metric)))
    if validity is not None:
        source_unit = validity['unit']
        if source_unit not in ALIASES.values():
            return None, None, None, 'unit_incompatible'
    if source_unit == "__disabled__":
        return None, None, None, "indicator_not_enabled"
    if source_unit == "__conflict__" or ("*", metric) in units:
        return None, None, None, "unit_conflict"
    if spec.get("confirmed") is True and profile.get("basis"):
        override = ALIASES.get(spec.get("unit"))
        # Explicit attestation may fill absent metadata but cannot silently override a conflict.
        if source_unit is not None and override != source_unit:
            return None, None, None, "unit_conflict"
        unit = override
    else:
        unit = source_unit
    if unit is None:
        return None, None, None, "unit_unknown"
    required_state = "ambient" if metric in {"pm10", "pm2_5"} else "reference_298.15K_101.325kPa"
    if metric in {"pm10", "pm2_5"} and unit in {"ppm", "ppb"}:
        return None, None, None, "unit_incompatible"
    state_confirmed = (spec.get("confirmed") is True and bool(profile.get("basis"))
                       and spec.get("state") == required_state)
    if not state_confirmed:
        if unit in {"ppm", "ppb"}:
            # Retain the verified native mixing-ratio unit for survey summaries.
            # No temperature/pressure assumption or AQI mass conversion is made.
            return raw, unit, None, "measurement_state_unconfirmed"
        # Mass-prefix conversion needs no temperature/pressure assumption.
        value = raw * (1000 if unit == "mg/m³" and metric != "co" else
                       .001 if unit == "μg/m³" and metric == "co" else 1)
        return value, UNITS[metric], None, "measurement_state_unconfirmed"
    if unit in {"ppm", "ppb"}:
        v = raw * MW[metric] / (8.314 * 298.15 / 101.325)
        if unit == "ppb":
            v /= 1000
        if metric != "co":
            v *= 1000
    else:
        v = raw * (1000 if unit == "mg/m³" and metric != "co" else
                   .001 if unit == "μg/m³" and metric == "co" else 1)
    # AQI retains its required native concentration units (CO: mg/m³).
    return v, UNITS[metric], v, None


def distance_m(row, location):
    try:
        lon, lat = float(row["lon"]), float(row["lat"])
        x, y = float(location["lon"]), float(location["lat"])
        if not all(math.isfinite(v) for v in (lon, lat, x, y)):
            return math.inf
        return math.hypot((lon - x) * math.cos(math.radians(y)), lat - y) * 111320
    except (KeyError, TypeError, ValueError):
        return math.inf


def fixed_eligible(row, profile):
    cadence = profile.get("sample_interval_seconds")
    location = profile.get("location", {})
    return (profile.get("mode") == "fixed_point" and profile.get("method_confirmed") is True
            and profile.get("interval_mean_confirmed") is True and bool(profile.get("basis"))
            and (profile.get("hangar_id") == row["hangar_id"]
                 or (row.get('source_site_id') and profile.get('hangar_id') == row['source_site_id']))
            and isinstance(cadence, int) and not isinstance(cadence, bool) and 1 <= cadence <= 60
            and 60 % cadence == 0 and row["time"].second % cadence == 0 and row["time"].microsecond == 0
            and isinstance(location.get("tolerance_m"), (int, float))
            and 0 < location["tolerance_m"] <= 50
            and distance_m(row, location) <= location["tolerance_m"])


def deduplicate(rows, qc):
    groups = defaultdict(list)
    for original in rows:
        row = dict(original)
        stamp = row.get("time")
        if not isinstance(stamp, datetime):
            qc["invalid_timestamp"] += 1
            continue
        if stamp.tzinfo:
            row["time"] = stamp.astimezone(TZ).replace(tzinfo=None)
        groups[(row["hangar_id"], row.get("load_sn"), row.get("load_type"), row["time"])].append(row)
    result = []
    for group in groups.values():
        row = dict(group[0])
        row["source_record_sha256"] = sorted(hashlib.sha256(str(r.get("id")).encode()).hexdigest() for r in group)
        qc["duplicate_rows"] += len(group) - 1
        if len({(r.get("lon"), r.get("lat"), r.get("alt"), r.get("task_id")) for r in group}) > 1:
            qc["conflicting_identity_or_location"] += len(group)
            continue
        for m in METRICS:
            values = {clean_number(r.get(m)) for r in group}
            if len(values) > 1:
                qc[f"conflicting_{m}"] += 1
                row[m] = None
        result.append(row)
    return result


def build_statistics(request: ReportRequest, data: Dataset, profiles=None):
    profiles = profiles or {}
    qc = Counter()
    if data.trend is not None:
        from .indicator_catalog import build_catalog
        data.indicator_catalog = build_catalog(data)
    def measure(row, metric, profile, configured):
        validity = (data.indicator_catalog['hangars'][row['hangar_id']][metric]
                    if data.indicator_catalog is not None else None)
        return measurement(row, metric, profile, configured, validity)
    units = configured_units(data.unit_configs)
    if data.project_indicators is not None:
        from .indicators import CODES, validate_indicators
        validate_indicators(data.project_indicators)
        enabled = {CODES[r['code']]: ALIASES[r['unit']] for r in data.project_indicators}
        units = {(kind, metric): enabled.get(metric, '__disabled__')
                 for kind in {r.get('unit_product_num', r['load_type']) for r in data.samples} for metric in METRICS}
    start, end = request.query_bounds()
    scoped = []
    for row in data.samples:
        if row.get("project_id") != request.project_id or row.get("hangar_id") not in request.hangar_ids:
            qc["out_of_scope"] += 1
            continue
        if isinstance(row.get("time"), datetime):
            stamp = row["time"].astimezone(TZ).replace(tzinfo=None) if row["time"].tzinfo else row["time"]
            if not start <= stamp < end:
                qc["outside_query_window"] += 1
                continue
        scoped.append(row)
    rows = deduplicate(scoped, qc)
    grouped = defaultdict(list)
    canonical = []
    for row in rows:
        grouped[(row["hangar_id"], row["time"].date())].append(row)
        # Reproducibility hash only. Coordinates and raw identifiers are not persisted in evidence.
        canonical.append({k: v.isoformat() if isinstance(v, datetime) else v
                          for k, v in row.items() if k not in {"id"}})
    source_hash = hashlib.sha256(json.dumps(sorted(canonical, key=lambda r: json.dumps(r, sort_keys=True, default=str)),
                                             ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    comparison_start, comparison_end = request.comparison()
    hangars, gaps = {}, []
    for hid in request.hangar_ids:
        day_results = {}
        for day in dates(comparison_start, request.end_date):
            samples = grouped[(hid, day)]
            if day < request.start_date:
                pa, pb = request.comparison_bounds()
                samples = [r for r in samples if pa <= r['time'] < pb]
            minute_values = {m: defaultdict(list) for m in METRICS}
            hour_values = {m: defaultdict(list) for m in METRICS}
            issues = Counter()
            streams = {(r.get("load_sn"), r.get("load_type")) for r in samples}
            fixed_day = bool(samples) and len(streams) == 1
            for row in samples:
                profile = profiles.get(profile_key(row), {})
                eligible = fixed_eligible(row, profile)
                fixed_day = fixed_day and eligible
                for m in METRICS:
                    value, unit, mass, reason = measure(row, m, profile, units)
                    if reason:
                        issues[f"{m}/{reason}"] += 1
                    if value is not None:
                        minute_values[m][(row["time"].replace(second=0, microsecond=0), unit)].append(value)
                    if mass is not None and eligible:
                        hour_values[m][row["time"].hour].append((mass, profile["sample_interval_seconds"]))
            ozone_limit = profiles.get(profile_key(samples[0]), {}).get("ozone_8h_limit", 160) if samples else 160
            if ozone_limit not in (100, 160):
                fixed_day = False
                ozone_limit = 160
            survey, concentrations, valid_hours = {}, {}, {}
            for m in METRICS:
                buckets = minute_values[m]
                metric_units = {k[1] for k in buckets}
                means = [mean(v) for v in buckets.values()]
                usable = len(metric_units) == 1 and bool(means)
                survey[m] = {"value": mean(means) if usable else None,
                             "unit": next(iter(metric_units)) if usable else None,
                             "minutes": len(buckets), "reason": None if usable else "缺少有效数据或单位不一致"}
                hours = {h: sum(v * seconds for v, seconds in values) / sum(s for _, s in values)
                         for h, values in hour_values[m].items() if 2700 <= sum(s for _, s in values) <= 3600}
                valid_hours[m] = len(hours) if fixed_day else 0
                if m == "o3":
                    value, windows = ozone_daily(hours, ozone_limit) if fixed_day else (None, 0)
                else:
                    value = mean(hours.values()) if fixed_day and len(hours) >= 20 else None
                concentrations[m] = float(rounded_concentration(m, value)) if value is not None else None
            result = calculate(concentrations)
            result.update(concentrations=concentrations, valid_hours=valid_hours,
                          ozone_valid_windows=windows, fixed_point_eligible=fixed_day,
                          reason=None if result["aqi"] is not None else
                          ("监测对象或采样口径未满足固定点要求" if not fixed_day else "有效时段或指标不足"))
            # Hourly survey summaries retain the same equal-minute weighting as
            # daily survey values; these are not regulatory hourly means.
            hourly = {}
            for hour in sorted({stamp.hour for buckets in minute_values.values() for stamp, _ in buckets}):
                values = {}
                for m in METRICS:
                    buckets = [(unit, mean(v)) for (stamp, unit), v in minute_values[m].items() if stamp.hour == hour]
                    unit_set = {unit for unit, _ in buckets}
                    usable = bool(buckets) and len(unit_set) == 1
                    values[m] = {'value': mean(v for _, v in buckets) if usable else None,
                                 'unit': next(iter(unit_set)) if usable else None, 'minutes': len(buckets)}
                hourly[f'{hour:02d}:00'] = values
            day_results[day.isoformat()] = {"samples": len(samples), "survey": survey, "standard": result, "hourly": hourly,
                                          "issues": dict(issues)}
            if request.report_type == 'DAY' and request.start_date <= day <= request.end_date:
                minute_series = {}
                for stamp in sorted({stamp for buckets in minute_values.values() for stamp, _ in buckets}):
                    minute_series[stamp.strftime('%H:%M')] = {}
                    for m in METRICS:
                        values = [(unit, mean(v)) for (t, unit), v in minute_values[m].items() if t == stamp]
                        usable = len(values) == 1
                        minute_series[stamp.strftime('%H:%M')][m] = {
                            'value': values[0][1] if usable else None, 'unit': values[0][0] if usable else None}
                day_results[day.isoformat()]['minute_series'] = minute_series
        def period(a, b):
            chosen = [day_results[d.isoformat()] for d in dates(a, b)]
            values = {}
            for m in METRICS:
                available = [r["survey"][m] for r in chosen if r["survey"][m]["value"] is not None]
                unit_set = {v["unit"] for v in available}
                values[m] = {"value": mean(v["value"] for v in available) if available and len(unit_set) == 1 else None,
                             "unit": next(iter(unit_set)) if len(unit_set) == 1 else None,
                             "days": len(available), "expected_days": len(chosen)}
            return {"start": a.isoformat(), "end": b.isoformat(), "survey": values,
                    "samples": sum(r["samples"] for r in chosen), "days": len(chosen),
                    "observed_days": sum(r["samples"] > 0 for r in chosen),
                    "valid_aqi_days": sum(r["standard"]["aqi"] is not None for r in chosen)}
        current, previous = period(request.start_date, request.end_date), period(comparison_start, comparison_end)
        if current["samples"] == 0:
            gaps.append({"hangar_id": hid, "code": "NO_DATA", "message": "所选机库在本期无有效关联采样"})
        if current["valid_aqi_days"] < current["days"]:
            gaps.append({"hangar_id": hid, "code": "AQI_GAPS", "message": "部分或全部日期不具备日 AQI 计算条件"})
        if previous["observed_days"] < previous["days"]:
            gaps.append({"hangar_id": hid, "code": "COMPARISON_GAPS", "message": "比较期采样覆盖不足，不生成总体升降结论"})
        if any(r["issues"] for d, r in day_results.items() if request.start_date.isoformat() <= d <= request.end_date.isoformat()):
            gaps.append({"hangar_id": hid, "code": "MEASUREMENT_GAPS", "message": "存在无效值、单位或测量状态未确认项，详见统计证据"})
        from .relative import evaluate, evidence_cards
        relative = evaluate(request, [r for r in rows if r['hangar_id'] == hid], profiles, units, measure, profile_key)
        hangars[hid] = {"name": data.hangars[hid], "days": day_results, "current": current, "previous": previous,
                        "report_type": request.report_type, "relative": relative}
        site_rows = [r for r in rows if r['hangar_id'] == hid]
        native_units = {m: set() for m in METRICS}
        for row in site_rows:
            profile = profiles.get(profile_key(row), {})
            for m in METRICS:
                value, unit, _, _ = measure(row, m, profile, units)
                if value is not None:
                    native_units[m].add(unit)
        six = evaluate(request, site_rows, profiles, units, measure, profile_key, panel=METRICS)
        six['units'] = {m: sorted(v) for m, v in native_units.items()}
        hangars[hid]['six_relative'] = six
        hangars[hid]["evidence_cards"] = evidence_cards(hangars[hid])
        if relative['current'] is None or relative['coverage'] < 1:
            gaps.append({"hangar_id": hid, "code": "RELATIVE_REFERENCE_GAPS", "message": relative['reason'] or "相对指数仅覆盖部分可比观测层"})
    if any(qc.values()):
        gaps.append({"code": "QUALITY_CONTROL", "message": "存在重复、冲突或范围外记录，已按指标排除"})
    if data.provenance.get("payload_exclusions"):
        gaps.append({"code": "PAYLOAD_GAPS", "message": "存在未匹配当前无人机大气传感器的采样，已排除并记录原因"})
    if any(data.exclusions.values()):
        gaps.append({"code": "RELATION_GAPS", "message": "项目查询时段存在无法归属的采样，未计入所选机库"})
    gaps.extend([{ "code": "OFFICIAL_COMPOSITE_UNAVAILABLE", "message": "综合相对指数及所选范围排名不属于正式环境空气质量综合指数评价"},
                 {"code": "FORECAST_UNAVAILABLE", "message": "预报数据待接入"}])
    result = {"schema_version": "1.0", "rules_version": VERSION,
            "data_kind": data.data_kind, "request": request.model_dump(mode="json"),
            "comparison": [comparison_start.isoformat(), comparison_end.isoformat()],
            "historical_recalculation": request.start_date < date(2026, 3, 1),
            "sample_sha256": source_hash,
            "provenance": data.provenance,
            "measurement_config_sha256": hashlib.sha256(json.dumps(profiles, sort_keys=True).encode()).hexdigest(),
            "unit_config_sha256": hashlib.sha256(json.dumps(data.project_indicators if data.project_indicators is not None else data.unit_configs, sort_keys=True, default=str).encode()).hexdigest(),
            "unit_source": "project_indicators_api" if data.project_indicators is not None else "database_config",
            "project_indicators": data.project_indicators,
            "quality": {k: v for k, v in qc.items() if v}, "excluded_project_rows": data.exclusions,
            "hangars": hangars, "gaps": gaps,
            "survey_basis": "日内有效分钟均值等权；区间内有数据日等权。仅代表采样覆盖，不是完整日均或地区评价。"}
    if data.trend is not None:
        from .hangar_trend import apply_statistics
        return apply_statistics(result, data)
    return result
