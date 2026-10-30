from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from airmore.service.city_public import parse_qweather

RAW=(Path(__file__).parent/'fixtures/regional/liaocheng-air-20260920.html').read_bytes()
NOW=datetime(2026,9,20,19,tzinfo=ZoneInfo('Asia/Shanghai'))

def test_provider_forecast_is_point_estimate_and_station_time_is_not_invented():
    forecasts,monitor=parse_qweather(RAW,'371500','聊城市',NOW)
    assert [f['aqi_min'] for f in forecasts]==[116,102,88,107,80]
    assert all(f['aqi_min']==f['aqi_max'] and not f['primaryPol'] for f in forecasts)
    assert len(monitor['stations'])==5
    assert '未标注观测时次' in monitor['note']

@pytest.mark.parametrize('city,now',[('郑州市',NOW),('聊城市',NOW.replace(day=21))])
def test_wrong_city_or_stale_forecast_is_rejected(city,now):
    with pytest.raises(ValueError,match='city or issue'):
        parse_qweather(RAW,'371500',city,now)

def test_incomplete_five_day_forecast_is_rejected():
    with pytest.raises(ValueError):
        parse_qweather(RAW.replace(b'09\xe6\x9c\x8824\xe6\x97\xa5',b'09\xe6\x9c\x8825\xe6\x97\xa5'),'371500','聊城市',NOW)
