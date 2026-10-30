from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from airmore.service.config import Settings
from airmore.service.models import METRICS, TZ
from airmore.service.selection_index import evaluate
from airmore.service.site_forecast import digest, load


def observations():
    def survey(offset):
        return {m: {'value': (i + 1) * offset, 'unit': 'ppm' if i < 4 else 'μg/m³'}
                for i, m in enumerate(METRICS)}
    hs = {s: {'current': {'survey': survey(i)}, 'previous': {'survey': survey(i / 2)},
              'days': {}, 'six_relative': {'strata': [], 'panel': list(METRICS)}}
          for s, i in [('a', 1), ('b', 2), ('c', 3)]}
    hs['b']['previous']['survey'] = {m: {'value': None, 'unit': None} for m in METRICS}
    return {'request': {'hangar_ids': list(hs), 'end_date': '2026-09-17'}, 'hangars': hs}


def test_existing_six_observations_form_pies_without_history_and_missing_previous_is_local():
    result = evaluate(observations())
    assert result['available']
    assert result['reference_basis'] == 'selected_current_observations'
    for i, s in enumerate(('a', 'b', 'c'), 1):
        rec = result['hangars'][s]['current']
        assert rec['value'] == pytest.approx(i * 50)
        assert sum(rec['ratios'].values()) == pytest.approx(1)
        assert set(rec['ratios']) == set(METRICS)
        assert rec['rank'] == i
    assert result['hangars']['b']['previous'] is None
    assert result['hangars']['a']['previous']['value'] == pytest.approx(25)
    assert result['hangars']['c']['previous']['value'] == pytest.approx(75)


def test_historical_report_uses_generation_day_forecast_with_explicit_issue_date(tmp_path):
    now = datetime(2026, 9, 20, 21, 30, tzinfo=TZ)
    days = [(now.date() + timedelta(days=i)).isoformat() for i in range(5)]
    payload = {'city_code': '410100', 'city_name': '郑州市', 'issue_date': '2026-09-20',
               'forecasts': [{'areaCode': '410100', 'dataDate': d.replace('-', ''),
                              'aqi_min': 50, 'aqi_max': 80, 'primaryPol': 'O3'} for d in days],
               'weather': [{'date': d, 'description': '多云', 'high': '26℃', 'low': '19',
                            'wind': '北风', 'force': '小于3级'} for d in days],
               'sources': [{'retrieved_at': now.isoformat(), 'sha256': 'fixture'}]}
    archive = tmp_path / 'forecast-archive/410100/2026-09-20.json'
    archive.parent.mkdir(parents=True)
    archive.write_text(json.dumps({'payload': payload, 'sha256': digest(payload)}))
    stats = {'request': {'end_date': '2026-09-17', 'hangar_ids': ['a'], 'project_id': 'p',
                         'as_of': now.isoformat()}}
    sites = [{'id': 'a', 'name': '设备', 'position': '郑州市', 'lng_lat_alt': None}]
    def no_network(*args):
        raise AssertionError('verified archive should avoid live requests')
    result = load(stats, Settings(state_dir=tmp_path, forecast_enabled=True), tmp_path / 'output',
                  read=no_network, sites=sites, project={}, now=now)
    assert len(result['forecasts']) == len(result['weather']) == 5
    assert result['issue_date'] == '2026-09-20'
    assert result['measurement_end_date'] == '2026-09-17'
    assert not result['gaps']
