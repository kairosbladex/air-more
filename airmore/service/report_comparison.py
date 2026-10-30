"""Deterministic display order and period comparisons for report advice."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .statistics import ALIASES
from .models import METRICS, survey_value_text

VERSION = 'report-period-comparison/v1'


def ranked_hangar_ids(stats, index, period='current'):
    metrics = stats.get('indicator_catalog', {}).get('table_metrics', METRICS)
    def key(hid):
        h = stats['hangars'][hid]
        source = h['days'][h['current']['end']] if period == 'last_day' else h[period]
        has_data = any(source['survey'].get(m, {}).get('value') is not None
                       and source['survey'][m].get('unit') for m in metrics)
        rank = index['hangars'][hid][period]['rank']
        return (not has_data, rank is None, rank or 0, hid)
    return sorted(stats['hangars'], key=key)


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except InvalidOperation:
        return None


def change(previous, current, reason=None, *, index=False):
    if reason:
        return {'delta': None, 'percent': None, 'change_text': reason}
    a, b = numeric(previous), numeric(current)
    if a is None or b is None:
        message = '两期缺测' if a is None and b is None else '比较期缺测' if a is None else '本期缺测'
        return {'delta': None, 'percent': None, 'change_text': message}
    delta = b-a
    direction = '上升' if delta > 0 else '下降' if delta < 0 else '持平'
    if index:
        text = direction if not delta else direction + f'{abs(delta):.0f}点'
        return {'delta': float(delta), 'percent': None, 'change_text': text}
    if not a:
        return {'delta': float(delta), 'percent': None,
                'change_text': '持平' if not delta else '比较期为0，未计环比'}
    percent = delta/a*100
    rounded = abs(percent).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    text = direction if not delta else direction + ('<0.01' if not rounded else f'{rounded:.2f}') + '%'
    return {'delta': float(delta), 'percent': float(percent), 'change_text': text}


def build_comparison(stats, index):
    rows = []
    metrics = stats.get('indicator_catalog', {}).get('table_metrics', METRICS)
    for hid in ranked_hangar_ids(stats, index):
        h = stats['hangars'][hid]
        previous, current = index['hangars'][hid]['previous'], index['hangars'][hid]['current']
        row = {'hangar_id': hid, 'hangar_name': h['name'], 'metric': 'index', 'label': '综合指数',
               'previous': previous['value'], 'current': current['value'],
               'previous_text': str(previous['value']) if previous['value'] is not None else '/',
               'current_text': str(current['value']) if current['value'] is not None else '/', 'unit': None}
        reason = '有效指标不一致' if previous['value'] is not None and current['value'] is not None and set(previous['terms']) != set(current['terms']) else None
        rows.append(row | change(previous['value'], current['value'], reason, index=True))
        for metric, label in (('pm10', 'PM10'), ('pm2_5', 'PM2.5')):
            if metric not in metrics:
                continue
            cells = [h[period]['survey'].get(metric, {}) for period in ('previous', 'current')]
            units = [ALIASES.get(cell.get('unit')) for cell in cells]
            validity = h.get('indicator_validity')
            valid = validity is None or validity.get(metric, {}).get('valid', False)
            values = [cell.get('value') if valid and unit else None for cell, unit in zip(cells, units)]
            texts = [survey_value_text(value, cell.get('unit'), cell.get('precision')) if value is not None else '/'
                     for cell, value in zip(cells, values)]
            unit = units[1] or units[0]
            reason = '单位无效' if not valid or not all(units) else '单位不一致' if units[0] != units[1] else None
            if reason == '单位不一致':
                texts = [t + ' ' + u if t != '/' else t for t, u in zip(texts, units)]
                unit = None
            rows.append({'hangar_id': hid, 'hangar_name': h['name'], 'metric': metric,
                         'label': label, 'unit': unit, 'previous': values[0], 'current': values[1],
                         'previous_text': texts[0], 'current_text': texts[1]} | change(*values, reason))
    particles = [r for r in rows if r['metric'] != 'index']
    comparable = [r for r in particles if r['percent'] is not None]
    available = [r for r in particles if numeric(r['current']) is not None and r['unit']]
    leads = {}
    focus = max(comparable, key=lambda r: abs(r['percent'])) if comparable else next(iter(available), None)
    if focus:
        identity = focus['hangar_name'] + '的' + focus['label']
        leads['particles'] = f"本期{identity}为{focus['current_text']} {focus['unit'] or ''}，"
        if focus['delta'] is not None:
            leads['particles'] += f"比较期为{focus['previous_text']} {focus['unit'] or ''}，{focus['change_text']}。"
        else:
            leads['particles'] += focus['change_text'] + '，暂不能计算环比。'
    else:
        leads['particles'] = '本期所选机库缺少有效颗粒物周期汇总，暂不能开展颗粒物环比。'
    for hid in ranked_hangar_ids(stats, index):
        record = index['hangars'][hid]['current']
        if record['value'] is not None:
            leads['comparison'] = f"本期{stats['hangars'][hid]['name']}综合指数为{record['value']}，在所选机库中列第{record['rank']}名。"
            break
    return {'version': VERSION, 'rows': rows, 'leads': leads,
            'current_range': [stats['request'].get('start_date'), stats['request']['end_date']],
            'previous_range': [next(iter(stats['hangars'].values()))['previous'].get(k) for k in ('start', 'end')],
            'percent_basis': '(current - previous) / previous * 100; matching valid units; positive previous only'}
