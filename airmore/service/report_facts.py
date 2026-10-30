"""Concise report facts shared by charts and evidence-bound prose."""
from .models import METRICS, survey_value_text
LABELS = dict(zip(METRICS, ('SO2','NO2','CO','O3','PM10','PM2.5')))


def series(h):
    c = h['current']
    if h['report_type'] == 'DAY':
        minutes = h['days'][c['end']].get('minute_series', {})
        if minutes:
            from datetime import datetime, timedelta
            start,end=[datetime.strptime(t,'%H:%M') for t in (min(minutes),max(minutes))]
            points=[]
            while start<=end:
                label=start.strftime('%H:%M');points.append((label,minutes.get(label,{})));start+=timedelta(minutes=1)
            return points, '分钟巡测均值'
        hours = h['days'][c['end']].get('hourly', {})
        if hours:
            return [(f'{hour:02d}:00', hours.get(f'{hour:02d}:00', {})) for hour in range(24)], '时段巡测均值'
    return [(d, v['survey']) for d, v in h['days'].items() if c['start'] <= d <= c['end']], '逐日巡测均值'


def fact_pack(h):
    c, r = h['current'], h['relative']
    facts = {}
    levels = [f"{LABELS[m]}巡测均值为{survey_value_text(c['survey'][m]['value'], c['survey'][m]['unit'])} {c['survey'][m]['unit']}" for m in ('pm10','pm2_5') if c['survey'][m]['value'] is not None]
    if levels:
        facts['particles'] = '本期' + '，'.join(levels) + '。'
    points, basis = series(h)
    peaks = []
    for m in ('pm10','pm2_5'):
        values = [(d, v[m]) for d,v in points if v.get(m, {}).get('value') is not None]
        if values:
            d,v = max(values, key=lambda item: item[1]['value'])
            time = d
            peaks.append(f"{LABELS[m]}最高{basis}为{survey_value_text(v['value'],v['unit'])} {v['unit']}，出现在{time}")
    if peaks:facts['peak'] = '；'.join(peaks)+'。'
    if r.get('changes'):
        ch=r['changes']
        facts['comparison'] = f"同条件可比观测的颗粒物相对指数由{ch['previous_value']:.2f}变为{ch['current_value']:.2f}；可比观测层占本期{r['coverage']:.2%}，仅代表这部分观测。"
    elif not r.get('current'):
        facts['reference'] = '本期可比历史资料不足，尚未形成颗粒物相对指数。'
    if c['observed_days'] < c['days']:
        facts['coverage'] = f"本期{c['days']}个日历日中有{c['observed_days']}日取得采样记录，其余日期无记录。"
    # Describe the observation time/height, never expose internal grid identifiers.
    repeats=[x for x in h.get('evidence_cards',[]) if x['kind']=='repeat']
    if repeats:
        import re
        value=re.sub(r'观测层\s*[^（(]+[（(]', '（', repeats[0]['text'])
        facts['hotspot']=value
    if len(facts)<2:
        facts['scope']='本期巡测结果仅代表实际采样覆盖，不能据此判断区域整体空气质量。'
    return facts
