"""Period facts for the approved product report, independent of Word and models."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re

FIELDS = ('SO2_24h', 'NO2_24h', 'CO_24h', 'O3_8h_24h', 'PM10_24h', 'PM2_5_24h')
LABELS = ('SO₂', 'NO₂', 'CO', 'O₃', 'PM₁₀', 'PM₂.₅')
NAMES = {'DAY': '日报', 'WEEK': '周报', 'MONTH': '月报'}
STYLE_VERSION = 'regional/v1'
BREAKS = ((0,50,150,475,800,1600,2100,2620), (0,40,80,180,280,565,750,940),
          (0,2,4,14,24,36,48,60), (0,100,160,215,265,800),
          (0,50,120,250,350,420,500,600), (0,35,60,115,150,250,350,500))
LEVELS = (0,50,100,150,200,300,400,500)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def window(kind, as_of):
    require(kind in NAMES, 'unknown period')
    end = as_of - timedelta(days=1)
    if kind == 'WEEK':
        end = as_of - timedelta(days=as_of.weekday() + 1)
        return end - timedelta(days=6), end
    return (end.replace(day=1) if kind == 'MONTH' else end), end


def iaqi(value, breaks):
    for i in range(1, len(breaks)):
        if value <= breaks[i]:
            return math.ceil((value-breaks[i-1]) / (breaks[i]-breaks[i-1]) *
                             (LEVELS[i]-LEVELS[i-1]) + LEVELS[i-1])
    raise ValueError('concentration outside reviewed interpolation range')


def _read_observations(path, city_code):
    rows = []
    for source in json.loads(path.read_text()):
        require(str(source['CityCode']) == city_code, 'wrong city')
        match = re.fullmatch(r'/Date\((\d+)\)/', source['TimePoint'])
        require(match is not None, 'invalid observation timestamp')
        day = datetime.fromtimestamp(int(match[1])/1000, ZoneInfo('Asia/Shanghai')).date()
        values = [float(source[f]) for f in FIELDS]
        require(all(math.isfinite(v) and v >= 0 for v in values), 'invalid measurement')
        parts = [iaqi(v, b) for v, b in zip(values, BREAKS)]
        aqi = float(source['AQI'])
        require(aqi.is_integer() and 0 <= aqi <= 500, 'invalid AQI')
        require(max(parts) == aqi, 'daily AQI reconciliation failed')
        rows.append({'date': day.isoformat(), 'values': values, 'iaqi': parts,
                     'aqi': int(aqi), 'primary': source['PrimaryPollutant']})
    require(len({r['date'] for r in rows}) == len(rows), 'duplicate observation day')
    return sorted(rows, key=lambda r: r['date'])


def load_observations(root, city_code='410100'):
    root = Path(root)
    metadata = json.loads((root / 'sources.json').read_text())
    verified = set()
    for source in metadata:
        if 'sha256' in source:
            require(hashlib.sha256((root/source['file']).read_bytes()).hexdigest() ==
                    source['sha256'], 'source snapshot changed: ' + source['file'])
            verified.add(source['file'])
    histories = {'zhengzhou-history.json'} | {
        s['file'] for s in metadata if s.get('role') == 'city-day-history'}
    require(histories <= verified, 'history snapshot lacks a hash record')
    by_date = {}
    for name in sorted(histories):
        for row in _read_observations(root/name, city_code):
            old = by_date.get(row['date'])
            require(old is None or old == row, 'conflicting history for ' + row['date'])
            by_date[row['date']] = row
    return sorted(by_date.values(), key=lambda r: r['date'])


def summarize(rows, start, end):
    selected = [r for r in rows if start.isoformat() <= r['date'] <= end.isoformat()]
    expected = (end-start).days + 1
    dates = [(start + timedelta(days=i)).isoformat() for i in range(expected)]
    missing = sorted(set(dates) - {r['date'] for r in selected})
    if not selected:
        return {'rows': [], 'missing': missing, 'expected_days': expected, 'days': 0}
    counts = [sum(lo <= r['aqi'] <= hi for r in selected)
              for lo, hi in ((0,50),(51,100),(101,150),(151,200),(201,300),(301,500))]
    totals = [sum(r['iaqi'][i] for r in selected) for i in range(6)]
    total = sum(totals)
    return {'rows': selected, 'missing': missing, 'expected_days': expected,
            'days': len(selected), 'counts': counts, 'good_days': sum(counts[:2]),
            'good_percent': round(100*sum(counts[:2])/len(selected), 1),
            'means': [sum(r['values'][i] for r in selected)/len(selected) for i in range(6)],
            'aqi_min': min(r['aqi'] for r in selected),
            'aqi_max': max(r['aqi'] for r in selected),
            'peak': max(selected, key=lambda r: (r['aqi'], r['date'])),
            'iaqi_totals': totals,
            'shares': [100*v/total if total else 0 for v in totals]}


def build_period(rows, kind, as_of):
    start, end = window(kind, as_of)
    current = summarize(rows, start, end)
    require(current['days'] > 0, 'no observations in requested period')
    if kind == 'MONTH':
        previous_end = start - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        # A partial month compares only an equally long part of the prior month.
        previous_end = min(previous_end, previous_start + timedelta(days=(end-start).days))
    else:
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=(end-start).days)
    previous = summarize(rows, previous_start, previous_end)
    comparable = (not current['missing'] and not previous['missing'] and
                  current['days'] == previous['days'])
    result = {'kind': kind, 'name': NAMES[kind], 'as_of': as_of.isoformat(),
              'start': start.isoformat(), 'end': end.isoformat(), 'current': current,
              'previous': previous, 'comparable': comparable,
              'previous_start': previous_start.isoformat(), 'previous_end': previous_end.isoformat()}
    result['facts_sha256'] = digest(result)
    return result


def advice_facts(period):
    s = period['current']
    issue = date.fromisoformat(period['as_of'])
    next_week = issue + timedelta(days=7-issue.weekday())
    next_month = (issue.replace(day=1)+timedelta(days=32)).replace(day=1)
    scope = f"郑州市{period['name']}；编制日期{period['as_of']}；统计时段{period['start']}至{period['end']}；有效{s['days']}天，应有{s['expected_days']}天。"
    if s['missing']:
        scope += '缺测日期：' + '、'.join(s['missing']) + '；统计仅基于有效日，不能认定为完整月评价。'
    return {
        'scope': scope,
        'ozone': f"本期AQI为{s['aqi_min']}至{s['aqi_max']}，优良{s['good_days']}天，轻度污染{s['counts'][2]}天。最高AQI日期{ s['peak']['date']}，首要污染物为{s['peak']['primary']}；不能据此认定具体污染来源。",
        'particles': f"本期PM10日值平均{s['means'][4]:.1f} μg/m³，PM2.5日值平均{s['means'][5]:.1f} μg/m³。缺少完整可比前期时，不能称已改善、下降或上升。",
        'action_scope': {
            'DAY': '建议供本班及下一轮工作安排使用，重点为复测、排查、同条件背景对照和结果复核。',
            'WEEK': f'建议供编制日期之后的下一完整周（{next_week.isoformat()}至{(next_week+timedelta(days=6)).isoformat()}）使用，重点为复测、排查、同条件背景对照和结果复核。',
            'MONTH': f'建议供编制日期之后的下月（{next_month.year}年{next_month.month}月）工作准备使用，重点为按本期已出现的问题安排复测、作业台账核查和数据质量复核；不得预测下月天气、AQI或宣称治理效果。'
        }[period['kind']]
    }
