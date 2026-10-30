"""Java hangarTrend windows, SQL order and numeric semantics, with report unit validity."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

from .models import METRICS, ReportError, dates
from .indicator_catalog import TREND_METRICS, TREND_LABELS

VERSION = 'java-hangarTrend/v2'
TABLES = {'DAY': 'bo_daily_air_report', 'WEEK': 'bo_weekly_air_report',
          'MONTH': 'bo_monthly_air_report'}


def bucket(day, kind):
    if not isinstance(day, date):
        day = date.fromisoformat(str(day)[:10])
    if isinstance(day, datetime):
        day = day.date()
    return (day - timedelta(days=day.weekday()) if kind == 'WEEK' else
            day.replace(day=1) if kind == 'MONTH' else day).isoformat()


def window(kind, end):
    """AirHangarTrendBuilder.Window: 15 days, 8 Monday weeks, 12 calendar months."""
    if kind == 'DAY':
        keys = [(end - timedelta(days=14-i)).isoformat() for i in range(15)]
        labels = keys[:]
    elif kind == 'WEEK':
        monday = date.fromisoformat(bucket(end, kind))
        keys = [(monday - timedelta(weeks=7-i)).isoformat() for i in range(8)]
        labels = [k + '~' + (date.fromisoformat(k)+timedelta(days=6)).isoformat() for k in keys]
    else:
        month = end.year*12+end.month-1
        keys = [date((month-11+i)//12, (month-11+i)%12+1, 1).isoformat() for i in range(12)]
        labels = [k[:7] for k in keys]
    return {'time_type': kind.lower(), 'keys': keys, 'labels': labels,
            'start': keys[0], 'end': end.isoformat()}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
        return float(value.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)) if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def mean(values):
    # Summary points have already been rounded by Java. Preserve decimal ties
    # during the existing equal-site/period aggregation (1.2344 + 1.2345)/2.
    return sum(Decimal(str(v)) for v in values) / len(values)


def read_snapshot(cur, request, limit, metrics=None):
    slots = ','.join(['%s'] * len(request.hangar_ids))
    metrics = [m for m in TREND_METRICS if m in (metrics if metrics is not None else METRICS)]
    fields = ''.join(','+m+'_avg' for m in metrics if m != 'ch4')

    def bounded():
        rows = list(cur.fetchall())
        if len(rows) > limit:
            raise ReportError('TOO_MUCH_DATA', '平台汇总记录超过读取上限，请缩小范围')
        return rows

    def read(kind, start, end):
        cur.execute(f'''SELECT id,project_id,hangar_id,minute_time,create_time,update_time{fields}
            FROM {TABLES[kind]} WHERE project_id=%s AND del_flag=0
            AND minute_time BETWEEN %s AND %s AND hangar_id IN ({slots})
            ORDER BY minute_time ASC,COALESCE(update_time,create_time) DESC,id DESC LIMIT %s''',
                    (request.project_id, bucket(start, kind), end.isoformat(), *request.hangar_ids, limit+1))
        return bounded()

    previous_start, previous_end = request.comparison()
    axis = window(request.report_type, request.end_date)
    daily = read('DAY', previous_start, request.end_date)
    periods = {p: read(request.report_type, a, b) for p, a, b in (
        ('current', request.start_date, request.end_date), ('previous', previous_start, previous_end))}
    series = read(request.report_type, date.fromisoformat(axis['start']), request.end_date)
    methane = []
    if 'ch4' in metrics:
        expression = {'DAY': "DATE_FORMAT(md.time,'%%Y-%%m-%%d')",
                      'WEEK': "DATE_FORMAT(DATE_SUB(DATE(md.time),INTERVAL WEEKDAY(md.time) DAY),'%%Y-%%m-%%d')",
                      'MONTH': "DATE_FORMAT(md.time,'%%Y-%%m-01')"}[request.report_type]
        # The independent methane source is aggregated before merging. Never join
        # raw methane rows to air rows, which would multiply both observations.
        cur.execute(f'''SELECT t.site_id AS hangar_id,md.project_id,{expression} AS minute_time,
            AVG(CASE WHEN md.concentration>=0 THEN md.concentration END) AS ch4_avg
            FROM bo_task_methane md INNER JOIN bo_task t ON t.id=md.task_id AND t.del_flag=0
            LEFT JOIN bo_uav_site s ON s.id=t.site_id AND s.del_flag=0
            WHERE md.project_id=%s AND md.del_flag=0 AND md.time BETWEEN %s AND %s
            AND t.project_id=%s AND s.project_id=%s AND t.site_id IN ({slots})
            GROUP BY t.site_id,s.name,md.project_id,minute_time ORDER BY minute_time ASC LIMIT %s''',
                    (request.project_id, datetime.combine(date.fromisoformat(axis['start']),time()),
                     datetime.combine(request.end_date,time(23,59,59)), request.project_id,
                     request.project_id, *request.hangar_ids, limit+1))
        methane = bounded()
    return {'version': VERSION, 'daily': daily, 'periods': periods,
            'axis': axis, 'series': series, 'methane': methane, 'metrics': metrics}


def _points(rows, kind, allowed_sites, project, metrics=METRICS):
    points = {}
    for row in rows:
        if row['hangar_id'] not in allowed_sites or row.get('project_id', project) != project:
            raise ReportError('INVALID_PROJECT_SCOPE', '平台汇总记录超出所选项目或站点')
        key = (row['hangar_id'], bucket(row['minute_time'], kind))
        points.setdefault(key, {m: number(row.get(m+'_avg')) for m in metrics})
    return points


def apply_statistics(stats, data):
    snapshot, source, catalog = data.trend, stats['request'], data.indicator_catalog
    kind = source['report_type']
    bindings = stats['provenance'].get('site_bindings', stats['provenance'].get('bindings', []))
    sites = {b['id'] for b in bindings}
    members = {hid: [b['id'] for b in bindings if b['physical_hangar_id'] == hid] for hid in stats['hangars']}
    project = source['project_id']
    metrics = catalog['metrics']
    daily = _points(snapshot['daily'], 'DAY', sites, project)
    periods = {p: _points(rows, kind, sites, project) for p, rows in snapshot['periods'].items()}
    axis = snapshot.get('axis') or window(kind, date.fromisoformat(source['end_date']))
    trend = _points(snapshot.get('series', []), kind, sites, project, metrics)
    for key, values in _points(snapshot.get('methane', []), kind, sites, project, ('ch4',)).items():
        trend.setdefault(key, {})['ch4'] = values['ch4']

    def survey(points, selected, keys, validity, panel=METRICS):
        values = {}
        for m in panel:
            available = []
            for key in keys:
                parts = [points.get((site, key), {}).get(m) for site in selected]
                if selected and validity[m]['valid'] and all(v is not None for v in parts):
                    available.append(mean(parts))
            values[m] = {'value': number(mean(available)) if available else None,
                         'unit': validity[m]['unit'], 'precision': 4, 'days': len(available),
                         'expected_days': len(keys), 'source': VERSION,
                         'reason': validity[m]['reason'] if not validity[m]['valid'] else
                                   None if available else 'platform_summary_missing'}
        return values

    for hid, h in stats['hangars'].items():
        selected, validity = members[hid], catalog['hangars'][hid]
        h['indicator_validity'] = validity
        h['raw_statistics'] = {'current': deepcopy(h['current']), 'previous': deepcopy(h['previous']),
                               'daily_survey': {d: deepcopy(v['survey']) for d, v in h['days'].items()}}
        h['statistics_source'] = VERSION
        for day, value in h['days'].items():
            value['survey'] = survey(daily, selected, [day], validity)
            value.pop('minute_series', None)
        for period in ('current', 'previous'):
            p = h[period]
            keys = sorted({bucket(d, kind) for d in dates(date.fromisoformat(p['start']), date.fromisoformat(p['end']))})
            p['survey'] = survey(periods[period], selected, keys, validity)
            p['summary_periods'], p['summary_source'] = keys, TABLES[kind]
            # Existence of scoped platform rows is distinct from valid units.
            # All-invalid units still produce a truthful report with empty cells.
            p['summary_available'] = any((s, k) in periods[period] for s in selected for k in keys)
            if any(p['survey'][m]['value'] is None for m in catalog['table_metrics']):
                stats['gaps'].append({'hangar_id': hid, 'period': period, 'code': 'PLATFORM_SUMMARY_GAPS',
                                      'message': '平台汇总或载荷单位缺项，未用其他数据回填'})
        h['platform_trend'] = dict(axis, series=[{
            'metric': m, 'name': TREND_LABELS.get(m, m.upper()), 'unit': validity[m]['unit'],
            'reason': validity[m]['reason'],
            'values': [survey(trend, selected, [key], validity, [m])[m]['value'] for key in axis['keys']]}
            for m in metrics])
        h['evidence_cards'] = {}
    stats['gaps'] = [g for g in stats['gaps'] if g['code'] not in ('OFFICIAL_COMPOSITE_UNAVAILABLE', 'RELATIVE_REFERENCE_GAPS')]
    stats['rules_version'] += ';' + VERSION + ';' + catalog['version']
    stats['statistics_source'], stats['indicator_catalog'] = VERSION, catalog
    stats['unit_source'] = 'registered_payload_project_config'
    stats['unit_config_sha256'] = hashlib.sha256(json.dumps(data.unit_configs, sort_keys=True, default=str).encode()).hexdigest()
    stats['survey_basis'] = '平台对应周期汇总；同站点同周期按Java排序取首条；四位小数HALF_UP；无有效载荷单位置空；所选完整站点等权。'
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    stats['summary_sha256'] = hashlib.sha256(encoded.encode()).hexdigest()
    stats['provenance']['hangar_trend'] = json.loads(encoded)
    stats['provenance']['sources'] = sorted(set(stats['provenance'].get('sources', [])) |
        {TABLES['DAY'], TABLES[kind]} | ({'bo_task_methane'} if 'ch4' in metrics else set()))
    return stats
