"""Internal survey index. Fixed panel, matched strata, day-balanced reference.

Grid cells are observation bins, never surveyed locations or named road segments.
No interpolation, source attribution, AQI or cross-site ranking is performed.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from hashlib import sha256
import json
import math
from statistics import mean, median

from .models import LABELS, survey_value_text

VERSION = 'survey-relative/v1'
PANEL = ('pm10', 'pm2_5')


def stratum(row):
    try:
        lon, lat, alt = (float(row[k]) for k in ('lon', 'lat', 'alt'))
        if not all(math.isfinite(v) for v in (lon, lat, alt)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        if not row.get('load_sn') or not row.get('load_type'):
            return None
        return (row['load_type'], row['load_sn'], math.floor(lon * 1000),
                math.floor(lat * 1000), math.floor(alt / 25), row['time'].hour // 3)
    except (ValueError, TypeError, KeyError):
        return None


def evaluate(request, rows, profiles, units, measurement, profile_key, *, panel=PANEL):
    # Keep the legacy particulate analysis independent of the approved six-item
    # report index; callers must explicitly request the complete panel.
    panel = tuple(panel)
    start = datetime.combine(request.start_date, time())
    end = request.query_bounds()[1]
    baseline_start = start - timedelta(days=30)
    prev_start, prev_end = request.comparison_bounds()
    buckets = defaultdict(lambda: defaultdict(list))
    invalid_strata = 0
    current_keys = set()
    panel_current_keys = set()
    for row in rows:
        key = stratum(row)
        if key is None:
            invalid_strata += 1
            continue
        if start <= row['time'] < end:
            current_keys.add(key)
        profile = profiles.get(profile_key(row), {})
        values = {m: measurement(row, m, profile, units)[0] for m in panel}
        # Panel membership never varies with missing values.
        if any(v is None for v in values.values()):
            continue
        stamp = row['time']
        period = 'current' if start <= stamp < end else None
        if period:
            panel_current_keys.add(key)
        periods = ([period] if period else [])
        if baseline_start <= stamp < start:
            periods.append('baseline')
        if prev_start <= stamp < prev_end:
            periods.append('previous')
        for period in periods:
            minute = stamp.replace(second=0, microsecond=0)
            for m, v in values.items():
                buckets[(period, key, stamp.date(), m)][minute].append(v)
    daily = {k: mean(mean(v) for v in minutes.values()) for k, minutes in buckets.items()}
    by_key = defaultdict(lambda: {p: {m: {} for m in panel} for p in ('baseline', 'current', 'previous')})
    for (period, key, day, metric), value in daily.items():
        by_key[key][period][metric][day] = value
    keys = sorted(current_keys)
    strata, rejected = [], []
    for key in keys:
        groups = by_key[key]
        n = min(len(v) for v in groups['baseline'].values())
        base = {m: median(v.values()) if v else None for m, v in groups['baseline'].items()}
        public = {'id': sha256(json.dumps(key).encode()).hexdigest()[:12],
                  'time_band': f'{key[-1]*3:02d}—{(key[-1]+1)*3:02d}时',
                  'altitude_band': f'{key[-2]*25}—{(key[-2]+1)*25}米', 'baseline_days': n}
        if key not in panel_current_keys:
            rejected.append(public | {'reason': '本期指定指标未同时有效'})
            continue
        if n < 7 or any(v is None or v <= 0 for v in base.values()):
            rejected.append(public | {'reason': '历史有效日少于7日' if n < 7 else '历史基准为零或缺失'})
            continue
        items = {p: {m: 100 / len(panel) * mean(v.values()) / base[m] for m, v in groups[p].items()}
                 for p in ('current', 'previous') if all(groups[p].values())}
        anomalies = {m: sorted(d.isoformat() for d, v in groups['current'][m].items() if v > 1.3 * base[m]) for m in panel}
        strata.append(public | {'reference': '30天历史参考' if n >= 14 else '短期参考',
                               'baseline': base, 'terms': items,
                               'observed_days': len(groups['current'][panel[0]]),
                               'anomaly_dates': anomalies})
    def aggregate(selected, period):
        if not selected:
            return None
        terms = {m: mean(s['terms'][period][m] for s in selected) for m in panel}
        total = sum(terms.values())
        return {'value': total, 'terms': terms,
                'ratios': {m: v / total for m, v in terms.items()} if total > 0 else None,
                'strata': len(selected)}
    current = aggregate(strata, 'current')
    matched = [s for s in strata if 'previous' in s['terms']]
    paired_current, previous = aggregate(matched, 'current'), aggregate(matched, 'previous')
    # Without paired observations compare to the same frozen historical baseline.
    a = paired_current or current
    b = previous or ({'value': 100, 'terms': {m: 100 / len(panel) for m in panel}} if current else None)
    changes = None
    if a and b:
        delta = {m: a['terms'][m] - b['terms'][m] for m in panel}
        positive = sum(max(v, 0) for v in delta.values())
        changes = {'basis': '前期同条件观测' if previous else '历史基准', 'net': sum(delta.values()),
                   'terms': delta, 'positive_ratios': {m: max(v, 0)/positive for m, v in delta.items()} if positive > 1e-9 else None,
                   'current_value': a['value'], 'previous_value': b['value'], 'matched_strata': len(matched)}
    return {'version': VERSION, 'name': '颗粒物巡测相对指数' if panel == PANEL else '六项历史参考', 'panel': list(panel),
            'weights': {m: 1/len(panel) for m in panel}, 'baseline_window': [baseline_start.isoformat(), start.isoformat()],
            'comparison_window': [prev_start.isoformat(), prev_end.isoformat()],
            'basis': '同站点、同传感器、0.001度网格、25米高度层、3小时时段；分钟均值、日期等权、历史日中位数、可比层等权。阈值为内部试运行配置。',
            'current': current, 'previous': previous, 'paired_current': paired_current, 'changes': changes,
            'strata': strata, 'rejected': rejected, 'invalid_stratum_rows': invalid_strata,
            'current_strata': len(keys), 'usable_strata': len(strata),
            'coverage': len(strata)/len(keys) if keys else 0,
            'reason': None if current else ('指定指标浓度单位或位置高度信息尚不足' if not panel_current_keys else '可比历史不足或基准非正值'),
            'limitations': '仅代表可比观测层；非正式AQI，不代表达标、污染源贡献或治理效果；缺测不视为正常。'}


def evidence_cards(hangar):
    """Each recommendation must cite one complete observation or a specific gap."""
    c, r = hangar['current'], hangar['relative']
    missing = [d for d, v in hangar['days'].items() if c['start'] <= d <= c['end'] and not v['samples']]
    cards = [{'id': 'coverage', 'text': f"{hangar['name']}本期有观测{c['observed_days']}日，统计区间{c['days']}日；缺测日期：{'、'.join(missing) if missing else '无'}。", 'kind': 'coverage'}]
    for m, item in c['survey'].items():
        if item['value'] is not None:
            cards.append({'id': 'level_'+m, 'text': f"{LABELS[m]}本期巡测统计值{survey_value_text(item['value'], item['unit'])} {item['unit']}，覆盖{item['days']}个日期。", 'kind': 'level'})
    if not r['current']:
        cards.append({'id': 'reference_gap', 'text': f"颗粒物巡测相对指数暂未形成：{r['reason']}；可用可比层{r['usable_strata']}/{r['current_strata']}。", 'kind': 'gap'})
    else:
        cards.append({'id': 'relative', 'text': f"颗粒物巡测相对指数{r['current']['value']:.2f}，可用可比层{r['usable_strata']}/{r['current_strata']}，仅代表这些观测层。", 'kind': 'relative'})
        for s in r['strata']:
            for m, ds in s['anomaly_dates'].items():
                if ds:
                    cards.append({'id': f"repeat_{s['id']}_{m}", 'text': f"观测层{s['id']}（{s['time_band']}、记录高度{s['altitude_band']}）的{LABELS[m]}在{ s['observed_days']}个有效日中有{len(ds)}日高于可比历史基准三成；偏高日期：{'、'.join(ds)}。同日多条记录不代表独立巡测复现。", 'kind': 'repeat'})
    repeats = sorted((c for c in cards if c['kind'] == 'repeat'), key=lambda c: c['id'])
    return [c for c in cards if c['kind'] != 'repeat'] + repeats[:6]
