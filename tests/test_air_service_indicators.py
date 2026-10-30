import io
import json

import pytest

from airmore.service.indicators import fetch_indicators, validate_indicators
from airmore.service.models import ReportError
from airmore.service.statistics import build_statistics
from airmore.service.examples import synthetic_case

RECORDS = [dict(code='SO2', name='SO2', unit='ppm', sort=1),
           dict(code='PM2.5', name='PM2.5', unit='μg/m³', sort=2)]


def test_project_get_uses_configured_identity_without_query():
    def open_url(req, timeout):
        assert req.full_url == 'https://example.test/airmonitoringNew/screen/indicators'
        assert req.get_method() == 'GET'
        assert req.get_header('X-project') == 'project-a'
        return io.BytesIO(json.dumps(RECORDS).encode())
    assert fetch_indicators('a', {'a': {'base_url': 'https://example.test',
        'headers': {'X-Project': 'project-a'}}}, open_url) == RECORDS


def test_configured_source_never_uses_other_project_or_silent_fallback():
    with pytest.raises(ReportError, match='项目身份未配置'):
        fetch_indicators('b', {'a': {}})
    def failed(*args, **kwargs):
        raise OSError('secret credential')
    with pytest.raises(ReportError) as error:
        fetch_indicators('a', {'a': {'base_url': 'https://example.test'}}, failed)
    assert 'secret' not in str(error.value)
    assert fetch_indicators('a', {}) is None


@pytest.mark.parametrize('records', [dict(data=RECORDS), RECORDS + [RECORDS[0]],
    [dict(code='PM10', name='PM10', unit='ppm', sort=1)],
    [dict(code='CO', name='CO', unit='unknown', sort=1)]])
def test_invalid_response_rejected(records):
    with pytest.raises(ValueError):
        validate_indicators(records)


def test_api_units_fill_values_and_disabled_metrics_stay_missing():
    req, data, _ = synthetic_case(1, start_date='2026-09-14', end_date='2026-09-14', report_type='DAY')
    data.project_indicators = RECORDS
    # A broken legacy configuration must not override the authoritative API.
    data.unit_configs = [{'index_name': 'so2', 'index_unit': 'invalid'}]
    stats = build_statistics(req, data)
    current = stats['hangars']['demo-1']['current']
    # API units do not attest the temperature/pressure state needed for ppm conversion.
    assert current['survey']['so2']['unit'] == 'ppm'
    assert current['survey']['so2']['value'] is not None
    assert stats['hangars']['demo-1']['days']['2026-09-14']['issues']['so2/measurement_state_unconfirmed'] > 0
    assert current['survey']['pm2_5']['unit'] == 'μg/m³'
    assert current['survey']['pm2_5']['value'] == 26
    assert current['survey']['co']['value'] is None
    assert current['valid_aqi_days'] == 0
    assert stats['unit_source'] == 'project_indicators_api'
    assert stats['project_indicators'] == RECORDS


def test_empty_enabled_list_does_not_reenable_profile_metrics():
    req, data, profiles = synthetic_case(1, missing=True)
    data.project_indicators = []
    stats = build_statistics(req, data, profiles)
    assert all(v['value'] is None for v in stats['hangars']['demo-1']['current']['survey'].values())


def test_backend_api_prefix_project_header_and_r_envelope():
    def open_url(req, timeout):
        assert req.full_url == 'https://example.test/api/airmonitoringNew/screen/indicators'
        assert req.get_header('Projectid') == 'project-a'
        assert req.get_header('Authorization') == 'Bearer test-only'
        return io.BytesIO(json.dumps({'success': True, 'code': 200, 'data': RECORDS}).encode())
    assert fetch_indicators('project-a', {'project-a': {
        'base_url': 'https://example.test/api/',
        'headers': {'Authorization': 'Bearer test-only'}}}, open_url) == RECORDS


@pytest.mark.parametrize('body', [
    {'success': False, 'code': 200, 'data': RECORDS},
    {'success': True, 'code': 401, 'data': RECORDS},
    {'success': True, 'code': 200, 'data': None},
])
def test_http_success_is_not_business_success(body):
    with pytest.raises(ReportError):
        fetch_indicators('a', {'a': {'base_url': 'https://example.test/api'}},
                         lambda *a, **k: io.BytesIO(json.dumps(body).encode()))


def test_wrong_project_header_rejected_before_request():
    def unexpected(*args, **kwargs):
        pytest.fail('must not send request with mismatched project identity')
    with pytest.raises(ReportError):
        fetch_indicators('a', {'a': {'base_url': 'https://example.test/api',
            'headers': {'PROJECTID': 'b'}}}, unexpected)
