"""Fetch independently scoped city facts for device reports, never device AQI.

Only city codes are sent to public providers. Source bytes, retrieval times and
hashes are retained in each report. Archived days are never relabelled as current.
"""
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from airmore.regional.product_periods import FIELDS, BREAKS, iaqi, summarize, digest
from .models import ReportError
from .repository import MySQLRepository

TZ = ZoneInfo('Asia/Shanghai')
CNEMC = 'https://air.cnemc.cn:18007'


def _read(url, post=False):
    req = Request(url, data=b'' if post else None, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=25) as response:
        body = response.read(8_000_001)
    if len(body) > 8_000_000:
        raise ValueError('public response too large')
    return body


def inside_ring(point, ring):
    x, y = point[:2]
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def contains(geometry, point):
    polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    return any(inside_ring(point, p[0]) and not any(inside_ring(point, h) for h in p[1:]) for p in polygons)


def resolve_sites(sites, cities, project, fetch):
    """Resolve each site's configured city; reject cross-city or uncertain joins."""
    city_names = {str(c['CityCode']): c['Area'] for c in cities}
    result = []
    for site in sites:
        position = site.get('position') or ''
        matches = [code for code, name in city_names.items() if name in position]
        candidate = matches[0] if len(matches) == 1 else str(project.get('city_code') or '')
        if not re.fullmatch(r'\d{6}', candidate) or candidate not in city_names:
            raise ReportError('CITY_SCOPE_UNKNOWN', '站点所属城市未确认，不能套用其他城市数据')
        raw = site.get('lng_lat_alt')
        coords = json.loads(raw)['geometry']['coordinates'] if raw else None
        if coords:
            if len(coords) < 2 or not all(math.isfinite(float(v)) for v in coords[:2]):
                raise ReportError('CITY_SCOPE_UNKNOWN', '站点配置坐标无效')
            boundary = json.loads(fetch('boundary-'+candidate+'.json',
                'https://geo.datav.aliyun.com/areas_v3/bound/'+candidate+'_full.json'))
            if not any(contains(f['geometry'], coords) for f in boundary['features']):
                raise ReportError('CITY_SCOPE_CONFLICT', '站点坐标与城市配置不一致，请核对站点所属城市')
        elif len(matches) != 1:
            raise ReportError('CITY_SCOPE_UNKNOWN', '站点缺少可核验的城市地址和坐标')
        result.append({'site_id': site['id'], 'name': site['name'], 'city_code': candidate,
                       'city_name': city_names[candidate],
                       'simulated': '模拟' in position or '联调' in site['name'],
                       'basis': 'configured_coordinate_boundary' if coords else 'site_address'})
    if len({s['city_code'] for s in result}) != 1:
        raise ReportError('MULTIPLE_CITY_SCOPE', '所选站点跨城市，请按城市分别导出报告')
    return result


def observations(raw, city_code):
    result = []
    for row in json.loads(raw):
        if str(row['CityCode']) != city_code:
            raise ValueError('city history scope mismatch')
        stamp = re.fullmatch(r'/Date\((\d+)\)/', row['TimePoint'])
        if not stamp:
            raise ValueError('invalid date')
        day = datetime.fromtimestamp(int(stamp[1])/1000, TZ).date().isoformat()
        values = [float(row[f]) for f in FIELDS]
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError('invalid city concentration')
        parts = [iaqi(v, b) for v, b in zip(values, BREAKS)]
        aqi = float(row['AQI'])
        if not aqi.is_integer() or max(parts) != aqi:
            raise ValueError('city AQI reconciliation failed')
        result.append({'date': day, 'values': values, 'iaqi': parts,
                       'aqi': int(aqi), 'primary': row['PrimaryPollutant']})
    if len({r['date'] for r in result}) != len(result):
        raise ValueError('duplicate city day')
    return sorted(result, key=lambda r: r['date'])


def make_period(rows, request, issue):
    start, end = date.fromisoformat(request['start_date']), date.fromisoformat(request['end_date'])
    kind = request['report_type']
    current = summarize(rows, start, end)
    if not current['days']:
        raise ReportError('CITY_HISTORY_UNAVAILABLE', '所选时段暂无城市日监测数据，请选择可用日期')
    trend_start = end-timedelta(days=13) if kind == 'DAY' else start
    trend = summarize(rows, trend_start, end)
    previous_end = start-timedelta(days=1)
    previous_start = previous_end-timedelta(days=(end-start).days)
    previous = summarize(rows, previous_start, previous_end)
    p = {'kind': kind, 'name': {'DAY':'日报','WEEK':'周报','MONTH':'月报'}[kind],
         'start': start.isoformat(), 'end': end.isoformat(), 'as_of': issue.isoformat(),
         'current': current, 'trend': trend, 'trend_start': trend_start.isoformat(),
         'previous': previous, 'previous_start': previous_start.isoformat(),
         'previous_end': previous_end.isoformat(),
         'comparable': not current['missing'] and not previous['missing']}
    p['facts_sha256'] = digest(p)
    return p


def select_forecasts(rows, city_code, issue):
    expected={(issue+timedelta(days=i)).strftime('%Y%m%d') for i in range(5)}
    selected, seen=[],set()
    for row in rows:
        if str(row['areaCode']) != city_code or row['dataDate'] in seen:
            raise ValueError('forecast city/date mismatch')
        seen.add(row['dataDate'])
        if row['dataDate'] in expected:
            if not 0 <= row['aqi_min'] <= row['aqi_max'] <= 500:
                raise ValueError('invalid forecast')
            selected.append(row)
    if len(selected)!=5:
        raise ValueError('incomplete or stale forecast')
    return sorted(selected,key=lambda f:f['dataDate'])


def load_context(stats, cfg, output, *, read=_read, sites=None, project=None, now=None):
    now = now or datetime.now(TZ)
    root = Path(output)/'regional-sources'
    root.mkdir(parents=True, exist_ok=True)
    sources, memo = [], {}
    def fetch(name, url, post=False):
        if name in memo:
            return memo[name]
        body = read(url, post)
        (root/name).write_bytes(body)
        sources.append({'file': name, 'url': url, 'method': 'POST' if post else 'GET',
                        'retrieved_at': now.isoformat(), 'sha256': hashlib.sha256(body).hexdigest()})
        memo[name] = body
        return body
    request = stats['request']
    ids = list(stats['hangars'])
    if sites is None:
        with MySQLRepository(cfg).session() as cursor:
            cursor.execute('SELECT id,name,lng_lat_alt,position FROM bo_uav_site WHERE del_flag=0 '
                           'AND project_id=%s AND id IN ('+','.join(['%s']*len(ids))+')',
                           (request['project_id'], *ids))
            sites = cursor.fetchall()
            cursor.execute('SELECT city_code,city_name FROM bo_project WHERE id=%s AND del_flag=0',
                           (request['project_id'],))
            project = cursor.fetchone() or {}
        if {s['id'] for s in sites} != set(ids):
            raise ReportError('CITY_SCOPE_UNKNOWN', '站点城市关联缺失')
    cities = json.loads(fetch('city-daily.json', CNEMC+'/CityData/GetAllCityDayAQIModels'))
    bindings = resolve_sites(sites, cities, project, fetch)
    code, name = bindings[0]['city_code'], bindings[0]['city_name']
    raw = fetch('history.json', CNEMC+'/HourChangesPublish/GetCityDayAqiHistoryByCondition?citycode='+code, True)
    rows = observations(raw, code)
    # Merge only previously verified city-day snapshots. No latest-day substitution.
    cache = Path(cfg.state_dir)/'city-history'/code
    cache.mkdir(parents=True, exist_ok=True)
    by_day, archives = {}, []
    for file in sorted(cache.glob('*.json')):
        prior = json.loads(file.read_text())
        if prior.get('city_code') == code and prior.get('sha256') == digest(prior['rows']):
            by_day.update({r['date']: r for r in prior['rows']})
            archives.append({'file':file.name,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                             'origin':prior.get('origin'),'rows_sha256':prior['sha256']})
            (root/('archive-'+file.name)).write_bytes(file.read_bytes())
    by_day.update({r['date']: r for r in rows})
    rows = sorted(by_day.values(), key=lambda r:r['date'])
    snapshot = {'city_code':code,'rows':rows,'sha256':digest(rows)}
    for published in cities:
        if str(published['CityCode']) == code:
            day=published['TimePoint'][:10]
            matched=next((r for r in rows if r['date']==day),None)
            if matched and matched['aqi'] != int(published['AQI']):
                raise ValueError('city daily cross-check failed')
    period = make_period(rows, request, now.date())
    temp=cache/'.next';temp.write_text(json.dumps(snapshot,ensure_ascii=False));temp.replace(cache/'verified.json')
    (root/'verified-history.json').write_text(json.dumps(snapshot,ensure_ascii=False))
    forecasts, weather, monitor, gaps = [], [], None, []
    if code == '410100':
        try:
            raw_forecast=json.loads(fetch('forecast.json','https://app.zhengzhou.gov.cn/api/zzepb/forecast/'))
            forecasts = select_forecasts(raw_forecast, code, now.date())
        except Exception as exc:
            forecasts=[];gaps.append('城市预报暂不可用：'+type(exc).__name__)
        try:
            from lxml import html
            tree=html.fromstring(fetch('weather.html','https://www.weather.com.cn/weather/101180101.shtml'))
            for i,li in enumerate(tree.xpath('//ul[contains(@class,"t clearfix")]/li')[:5]):
                expected=now.date()+timedelta(days=i)
                if int(re.search(r'\d+',li.xpath('./h1')[0].text_content())[0]) != expected.day:
                    raise ValueError('weather date mismatch')
                high=li.xpath('./p[@class="tem"]/span/text()')
                weather.append({'date':expected.isoformat(),'description':li.xpath('./p[@class="wea"]')[0].text_content(),
                    'high':high[0] if high else None,'low':li.xpath('./p[@class="tem"]/i')[0].text_content().replace('℃',''),
                    'wind':'转'.join(dict.fromkeys(li.xpath('./p[@class="win"]/em/span/@title'))),
                    'force':li.xpath('./p[@class="win"]/i')[0].text_content().replace('<','小于')})
        except Exception as exc:
            weather=[];gaps.append('天气预报暂不可用：'+type(exc).__name__)
        try:
            candidate=json.loads(fetch('monitor.json','https://app.zhengzhou.gov.cn/api/zzepb/monitor/hour/'))
            stamp=datetime.fromisoformat(candidate['data'][0]).replace(tzinfo=TZ)
            if not 0 <= (now-stamp).total_seconds() <= 86400:
                raise ValueError('station observations stale')
            monitor=candidate
        except Exception as exc:
            gaps.append('监测站实况暂不可用：'+type(exc).__name__)
    else:
        gaps.append('该城市的正式预报与天气资料尚未接入')
    (root/'sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2))
    return {'city_code':code,'city_name':name,'bindings':bindings,'period':period,
            'forecasts':forecasts,'weather':weather,'monitor':monitor,'gaps':gaps,
            'sources':sources,'archives':archives,'retrieved_at':now.isoformat()}


def advice_facts(context, stats):
    p=context['period'];s=p['current'];city=context['city_name'];peak=s['peak']
    horizon={'DAY':'本班及下一轮','WEEK':'编制日之后的下一完整周','MONTH':'编制日之后的下月'}[p['kind']]
    return {
        'city': f"{city}，统计时段{p['start']}至{p['end']}，{s['days']}个有效日，AQI范围{s['aqi_min']}至{s['aqi_max']}，优良天数占比{s['good_percent']}%。最高日{peak['date']}首要污染物{peak['primary']}。臭氧累计分指数占比{s['shares'][3]:.1f}%，不是污染源贡献率。建议面向{horizon}，不认定污染来源。",
        'particles': f"城市颗粒物日值平均：PM10为{s['means'][4]:.1f} μg/m³，PM2.5为{s['means'][5]:.1f} μg/m³。缺测日期{p['current']['missing']}，不能据此认定具体污染源或治理效果。",
        'forecast': '编制日'+p['as_of']+'。城市预报：'+json.dumps(context['forecasts'],ensure_ascii=False)+'；天气：'+json.dumps(context['weather'],ensure_ascii=False)+'。均为预测，不是实测。'+({'DAY':'建议面向本班及下一轮。','WEEK':'下周工作须在执行时重新核对届时预报。','MONTH':'下月工作须在执行时重新核对届时预报，当前预报不代表下月天气。'}[p['kind']]),
        'site': ('所选站点含联调模拟数据，不能代表真实现场污染，须先核实实际位置、仪器单位和采样条件。' if any(b['simulated'] for b in context['bindings']) else '城市数据与所选站点巡测分别统计，不能将城市AQI赋给设备；核查高值时须同时对照区域背景与实际采样条件。')+'不得要求补填不存在的记录。'
    }
