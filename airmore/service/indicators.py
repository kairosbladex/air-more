"""Read project-scoped enabled indicators using server-owned credentials."""
import json
import urllib.request
from urllib.parse import urlsplit

from .models import METRICS, ReportError

CODES = {'SO2': 'so2', 'NO2': 'no2', 'CO': 'co', 'O3': 'o3', 'PM10': 'pm10', 'PM2.5': 'pm2_5'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_indicators(project_id, configs, opener=None):
    """None means legacy mode; configured mode never falls back on failure."""
    if not configs:
        return None
    config = configs.get(project_id)
    if not isinstance(config, dict):
        raise ReportError('INDICATORS_PROJECT_UNCONFIGURED', '项目指标接口的项目身份未配置')
    base = config.get('base_url', '')
    parsed = urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise ReportError('INDICATORS_CONFIG_INVALID', '项目指标接口地址配置无效')
    try:
        headers = dict(config.get('headers', {}))
        project_headers = [key for key in headers if key.lower() == 'projectid']
        if any(headers[key] != project_id for key in project_headers):
            raise ValueError('project identity mismatch')
        for key in project_headers:
            del headers[key]
        headers['projectId'] = project_id
        path = config.get('path', '/airmonitoringNew/screen/indicators')
        if not isinstance(path, str) or path not in {'/screen/indicators', '/airmonitoringNew/screen/indicators'}:
            raise ValueError('unsupported indicators path')
        request = urllib.request.Request(base.rstrip('/') + path,
                                         headers=headers, method='GET')
        open_url = opener or urllib.request.build_opener(NoRedirect()).open
        with open_url(request, timeout=10) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('oversized')
        records = json.loads(raw)
        if isinstance(records, dict):
            if records.get('success') is not True or type(records.get('code')) is not int or records['code'] != 200:
                raise ValueError('business response failed')
            records = records.get('data')
        validate_indicators(records)
        return records
    except Exception:
        raise ReportError('INDICATORS_UNAVAILABLE', '项目指标接口读取或校验失败') from None


def validate_indicators(records):
    from .statistics import ALIASES
    if not isinstance(records, list) or len(records) > len(METRICS):
        raise ValueError('invalid indicators')
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('invalid indicator')
        metric = CODES.get(record.get('code'))
        unit = record.get('unit')
        if metric is None or metric in seen or not isinstance(unit, str) or unit not in ALIASES:
            raise ValueError('unknown or duplicate indicator/unit')
        if metric in {'pm10', 'pm2_5'} and ALIASES[unit] in {'ppm', 'ppb'}:
            raise ValueError('incompatible particulate unit')
        if not isinstance(record.get('name'), str) or type(record.get('sort')) is not int:
            raise ValueError('invalid metadata')
        seen.add(metric)
