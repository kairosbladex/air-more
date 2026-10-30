"""City-scoped forecast enrichment; never substitutes city observations for sites.

The issue date is the report generation date, independent of its observation
period. Past generation dates use verified snapshots only. All requests use
public city codes, not device identities or coordinates. Provider failure leaves
a forecast gap while site measurements remain usable.
"""
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from lxml import html

from .models import TZ
from .regional_context import _read, resolve_sites, select_forecasts
from .repository import MySQLRepository

VERSION = 'selected-site-forecast/v2'


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def parse_weather(raw, issue):
    tree = html.fromstring(raw)
    rows = []
    for i, li in enumerate(tree.xpath('//ul[contains(@class,"t clearfix")]/li')[:5]):
        day = issue + timedelta(days=i)
        if int(re.search(r'\d+', li.xpath('./h1')[0].text_content())[0]) != day.day:
            raise ValueError('weather date mismatch')
        high = li.xpath('./p[@class="tem"]/span/text()')
        rows.append({'date': day.isoformat(), 'description': li.xpath('./p[@class="wea"]')[0].text_content(),
                     'high': high[0] if high else None, 'low': li.xpath('./p[@class="tem"]/i')[0].text_content().replace('℃',''),
                     'wind': '转'.join(dict.fromkeys(li.xpath('./p[@class="win"]/em/span/@title'))),
                     'force': li.xpath('./p[@class="win"]/i')[0].text_content().replace('<','小于')})
    if len(rows) != 5:
        raise ValueError('weather window incomplete')
    return rows


def validate_snapshot(body, code, issue):
    if body.get('sha256') != digest(body.get('payload')):
        raise ValueError('forecast archive checksum mismatch')
    value = body['payload']
    if value['city_code'] != code or value['issue_date'] != issue.isoformat():
        raise ValueError('forecast archive scope mismatch')
    select_forecasts(value['forecasts'], code, issue)
    expected = [(issue+timedelta(days=i)).isoformat() for i in range(5)]
    if [r['date'] for r in value['weather']] != expected:
        raise ValueError('weather archive scope mismatch')
    if not value.get('sources') or any(datetime.fromisoformat(s['retrieved_at']).astimezone(TZ).date() != issue
                                      for s in value['sources']):
        raise ValueError('forecast archive issue mismatch')
    return value


def load(stats, cfg, output, *, read=_read, sites=None, project=None, now=None):
    now = now or datetime.now(TZ)
    stamp = stats['request'].get('as_of')
    issue = datetime.fromisoformat(stamp).astimezone(TZ).date() if stamp else now.date()
    result = {'version': VERSION, 'issue_date': issue.isoformat(), 'city_name': '',
              'measurement_end_date': stats['request']['end_date'], 'issue_basis': 'generation_date',
              'forecasts': [], 'weather': [], 'sources': [], 'gaps': []}
    if not cfg.forecast_enabled:
        result['gaps'].append('预报数据未启用')
        return result
    root = Path(output)/'forecast-sources'; root.mkdir(parents=True, exist_ok=True)
    def fetch(name, url, post=False):
        raw = read(url, post)
        (root/name).write_bytes(raw)
        result['sources'].append({'file': name, 'url': url, 'retrieved_at': now.isoformat(),
                                  'sha256': hashlib.sha256(raw).hexdigest()})
        return raw
    try:
        ids = stats.get('provenance', {}).get('selected_site_ids', stats['request']['hangar_ids'])
        if sites is None:
            with MySQLRepository(cfg).session() as cur:
                cur.execute('SELECT id,name,lng_lat_alt,position FROM bo_uav_site WHERE del_flag=0 AND project_id=%s '
                            'AND id IN ('+','.join(['%s']*len(ids))+')', (stats['request']['project_id'], *ids))
                sites = cur.fetchall()
                cur.execute('SELECT city_code,city_name FROM bo_project WHERE id=%s AND del_flag=0', (stats['request']['project_id'],))
                project = cur.fetchone() or {}
        if {s['id'] for s in sites} != set(ids):
            raise ValueError('forecast site scope mismatch')
        # Address/project scope can resolve supported cities without city observations.
        # Boundaries still validate configured coordinates through resolve_sites.
        cities = [{'CityCode': 410100, 'Area': '郑州市'}, {'CityCode': 371500, 'Area': '聊城市'}]
        bindings = resolve_sites(sites, cities, project or {}, fetch)
        code, name = bindings[0]['city_code'], bindings[0]['city_name']
        result.update(city_code=code, city_name=name, bindings=bindings)
        cache = cfg.state_dir/'forecast-archive'/code/(issue.isoformat()+'.json')
        if cache.is_file():
            archived = validate_snapshot(json.loads(cache.read_text()), code, issue)
            (root/'archive.json').write_bytes(cache.read_bytes())
            result.update(archived, archive_sha256=hashlib.sha256(cache.read_bytes()).hexdigest())
        elif issue != now.date():
            result['gaps'].append('该报告日期的历史预报资料暂不可用')
        else:
            if code == '410100':
                rows = select_forecasts(json.loads(fetch('forecast.json','https://app.zhengzhou.gov.cn/api/zzepb/forecast/')), code, issue)
                weather_code = '101180101'
            elif code == '371500':
                from .city_public import parse_qweather
                rows, _ = parse_qweather(fetch('city-air.html','https://www.qweather.com/air/liaocheng-101121701.html'), code, name, now)
                rows = select_forecasts(rows, code, issue)
                weather_code = '101121701'
            else:
                raise ValueError('unsupported forecast city')
            weather = parse_weather(fetch('weather.html',f'https://www.weather.com.cn/weather/{weather_code}.shtml'), issue)
            result.update(forecasts=rows, weather=weather)
            payload = {k: result[k] for k in ('city_code','city_name','issue_date','forecasts','weather','sources')}
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_suffix('.next');temp.write_text(json.dumps({'payload':payload,'sha256':digest(payload)},ensure_ascii=False));temp.replace(cache)
    except Exception as exc:
        result.update(forecasts=[], weather=[])
        result['gaps'].append('预报资料暂不可用：'+type(exc).__name__)
    (root/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
