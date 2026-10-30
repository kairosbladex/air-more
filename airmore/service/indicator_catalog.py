"""Project indicators and exact registered-product units shared by all report views."""
import json

from .models import METRICS, LABELS

# Java AirDataParVO readable *Avg properties, mapped to SQL column names.
TREND_METRICS = ('t', 'h', 'p', 'pm10', 'pm2_5', 'pm1_0', 'co', 'so2', 'no2',
                 'o3', 'vocs', 'ox', 'cxhy', 'hs2', 'hcl', 'tsp', 'hcn', 'h2',
                 'ph3', 'cl2', 'wdr', 'ws', 'nh3', 'ch4')
TREND_LABELS = dict(LABELS, pm1_0='PM₁.₀', ox='OX', ch4='CH₄')
VERSION = 'registered-product-indicators/v1'


def normalize(name):
    value = str(name or '').strip().lower().translate(str.maketrans('₂₃₄', '234')).replace('.', '_')
    return 'hs2' if value == 'h2s' else value


def unit_name(value):
    from .statistics import ALIASES
    value = str(value or '').strip()
    return ALIASES.get(value, value) if value not in ('', '-', '/', '未知', 'unknown', 'null') else None


def product_unit(records, metric, product):
    """Never select the first product or fill missing registration with defaults."""
    if not product:
        return None, 'payload_product_missing'
    found = []
    for record in records:
        try:
            entries = json.loads(record.get('index_unit') or '[]')
            if not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries):
                return None, 'unit_config_invalid'
        except (ValueError, TypeError):
            return None, 'unit_config_invalid'
        for entry in entries:
            if entry.get('productNum') != product:
                continue
            code, named = entry.get('defaultUnitCode'), unit_name(entry.get('defaultUnit'))
            if code:
                matches = [unit_name(d.get('value')) for d in record.get('unit_dictionary', [])
                           if str(d.get('code')) == str(code)]
                if len(matches) != 1 or not matches[0] or (named and named != matches[0]):
                    return None, 'unit_dictionary_conflict'
                found.append(matches[0])
            else:
                found.append(named)
    if not found or None in found:
        return None, 'payload_unit_missing'
    if len(set(found)) != 1:
        return None, 'unit_conflict'
    unit = found[0]
    if metric in ('pm10', 'pm2_5', 'pm1_0') and unit in ('ppm', 'ppb'):
        return None, 'unit_incompatible'
    return unit, None


def build_catalog(data):
    records = [r for r in data.unit_configs if r.get('del_flag', 0) in (0, False)
               and r.get('index_status', 1) == 1]
    records.sort(key=lambda r: (r.get('sort') is None, r.get('sort') or 0, r.get('index_name') or ''))
    by_metric = {}
    for record in records:
        metric = normalize(record.get('index_name'))
        if metric in TREND_METRICS:
            by_metric.setdefault(metric, []).append(record)
    bindings = data.provenance.get('site_bindings', data.provenance.get('bindings', []))
    payloads = data.provenance.get('registered_payloads', data.provenance.get('payloads', []))
    sites, hangars = {}, {}
    for binding in bindings:
        products = {p.get('load_brand_code') for p in payloads if p.get('uav_id') == binding.get('uav_id')}
        values = {}
        for metric in TREND_METRICS:
            choices = [product_unit(by_metric.get(metric, []), metric, product) for product in products]
            units = {u for u, _ in choices}
            valid = metric in by_metric and bool(choices) and len(units) == 1 and None not in units
            reason = (None if valid else 'indicator_not_enabled' if metric not in by_metric else
                      next((r for _, r in choices if r), 'unit_conflict' if choices else 'payload_product_missing'))
            values[metric] = {'enabled': metric in by_metric, 'valid': valid,
                              'unit': next(iter(units)) if valid else None, 'reason': reason}
        sites[binding['id']] = values
        hangars.setdefault(binding['physical_hangar_id'], []).append(binding['id'])
    grouped = {}
    for hid, members in hangars.items():
        grouped[hid] = {}
        for metric in TREND_METRICS:
            parts = [sites[s][metric] for s in members]
            units = {p['unit'] for p in parts}
            valid = all(p['valid'] for p in parts) and len(units) == 1
            grouped[hid][metric] = {'enabled': metric in by_metric, 'valid': valid,
                'unit': next(iter(units)) if valid else None,
                'reason': None if valid else next((p['reason'] for p in parts if p['reason']), 'unit_conflict')}
    return {'version': VERSION, 'metrics': list(by_metric),
            'table_metrics': [m for m in METRICS if m in by_metric],
            'sites': sites, 'hangars': grouped,
            'configuration': json.loads(json.dumps(records, default=str))}
