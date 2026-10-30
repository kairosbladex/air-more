"""按城市和发布日期核验公开预报，不将页面采集时间作为观测时次。"""
from datetime import timedelta
import json
import math
import re
from lxml import html


def parse_weather(raw, now):
    tree = html.fromstring(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    result = []
    for i, li in enumerate(tree.xpath('//ul[contains(@class,"t clearfix")]/li')[:5]):
        day = now.date() + timedelta(days=i)
        if int(re.search(r'\d+', li.xpath('./h1')[0].text_content())[0]) != day.day:
            raise ValueError('weather date mismatch')
        high = li.xpath('./p[@class="tem"]/span/text()')
        result.append({'date': day.isoformat(), 'description': li.xpath('./p[@class="wea"]')[0].text_content(),
                       'high': high[0] if high else None,
                       'low': li.xpath('./p[@class="tem"]/i')[0].text_content().replace('℃', ''),
                       'wind': '转'.join(dict.fromkeys(li.xpath('./p[@class="win"]/em/span/@title'))),
                       'force': li.xpath('./p[@class="win"]/i')[0].text_content().replace('<', '小于')})
    if len(result) != 5:
        raise ValueError('incomplete weather forecast')
    return result


def parse_qweather(raw, city_code, city_name, now):
    tree = html.fromstring(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    title = ''.join(tree.xpath('//title/text()'))
    issued = tree.xpath('//span[@class="c-submenu__date"]/text()')
    if city_name not in title or issued != [now.date().isoformat()]:
        raise ValueError('forecast city or issue date mismatch')
    forecasts = []
    for i, row in enumerate(tree.xpath('//div[contains(@class,"city-air-tabs__row")]')[:5]):
        day = now.date() + timedelta(days=i)
        labels = row.xpath('./div[@class="date"]/p/text()')
        if labels[-1].strip() != day.strftime('%m月%d日'):
            raise ValueError('forecast day mismatch')
        aqi = int(row.xpath('.//span[@class="aqi"]/text()')[0])
        if not 0 <= aqi <= 500:
            raise ValueError('invalid forecast AQI')
        forecasts.append({'areaCode': city_code, 'dataDate': day.strftime('%Y%m%d'),
                          'aqi_min': aqi, 'aqi_max': aqi, 'primaryPol': '',
                          'provider': '和风天气', 'forecast_kind': 'provider_point_estimate'})
    if len(forecasts) != 5:
        raise ValueError('incomplete city forecast')
    # 页面没有观测时次，明确标为抓取时的发布值；不用于历史日统计。
    rows = tree.xpath('//div[contains(@class,"station__row")]')
    names, values = {}, {}
    fields = {'PM2.5': 'PM25', 'PM10': 'PM10', 'O3': 'O3', 'CO': 'CO', 'SO2': 'SO2', 'NO2': 'NO2'}
    for i, row in enumerate(rows):
        key = str(i)
        names[key] = row.xpath('./div[@class="air-station__city"]/p/text()')[0].strip()
        aqi_text = row.xpath('./div[@class="air-station__city"]/span')[0].text_content()
        item = {'AQI': int(re.findall(r'\d+', aqi_text)[-1])}
        for cell in row.xpath('.//div[@class="air-station-list__item"]'):
            metric = cell.xpath('./p[@class="aqi-title"]')[0].text_content().strip()
            number = float(re.search(r'\d+(?:\.\d+)?', cell.xpath('./p[@class="value"]')[0].text_content())[0])
            if not math.isfinite(number) or number < 0:
                raise ValueError('invalid station concentration')
            item[fields[metric]] = number
        if set(item) != {'AQI', *fields.values()}:
            raise ValueError('incomplete station observations')
        values[key] = item
    if not rows:
        raise ValueError('station observations missing')
    monitor = {'data': [now.strftime('%Y-%m-%d %H:%M'), values], 'stations': names,
               'caption': '城市监测站发布值（和风天气）',
               'note': '页面采集时间：'+now.strftime('%Y-%m-%d %H:%M')+'。来源未标注观测时次；仅供当前背景参考，不混入报告统计期。'}
    return forecasts, monitor
