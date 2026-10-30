"""User-defined report index: HJ633 arithmetic on platform period concentrations.

The visible label is 综合指数. This business calculation is not a qualified daily
AQI or the weighted comprehensive index of HJ663 Annex C. Original qualified
daily assessments remain in statistics.days.standard for independent evidence.
"""
from decimal import Decimal, InvalidOperation

from .aqi import calculate, rounded_concentration
from .models import METRICS, UNITS
from .statistics import ALIASES, MW

VERSION = 'report-platform-hj633-index/3'
# Same reference state and molecular weights as the existing AQI converter.
MOLAR_VOLUME = Decimal('8.314') * Decimal('298.15') / Decimal('101.325')


def mass_concentration(metric, value, unit):
    """Convert registered native units to HJ633 table units, never invent units."""
    if isinstance(value, bool):
        return None, 'invalid_concentration'
    try:
        concentration = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, 'missing_or_invalid_concentration'
    if not concentration.is_finite() or concentration < 0:
        return None, 'invalid_concentration'
    unit = ALIASES.get(unit)
    if unit is None:
        return None, 'unit_unknown'
    if unit in ('ppm', 'ppb'):
        if metric not in MW:
            return None, 'unit_incompatible'
        concentration *= Decimal(str(MW[metric])) / MOLAR_VOLUME
        if unit == 'ppb':
            concentration /= 1000
        if metric != 'co':
            concentration *= 1000
    elif unit != UNITS[metric]:
        concentration *= Decimal('.001') if metric == 'co' else 1000
    return concentration, None


def period_record(hangar, survey):
    concentrations, inputs, excluded = {}, {}, {}
    panel = hangar.get('indicator_validity')
    for metric in METRICS:
        cell = survey.get(metric, {})
        if panel is not None:
            validity = panel.get(metric, {})
            if not validity.get('valid'):
                excluded[metric] = validity.get('reason') or 'indicator_or_unit_invalid'
                continue
            if ALIASES.get(cell.get('unit')) != ALIASES.get(validity.get('unit')):
                excluded[metric] = 'unit_conflict'
                continue
        value, reason = mass_concentration(metric, cell.get('value'), cell.get('unit'))
        if reason:
            excluded[metric] = reason
            continue
        concentrations[metric] = value
        inputs[metric] = {'value': cell['value'], 'unit': cell['unit'],
                          'mass_value': float(value), 'mass_unit': UNITS[metric],
                          'rounded_mass_value': float(rounded_concentration(metric, value))}
    # DAY, WEEK and MONTH are report windows, never hourly AQI windows.
    # Use the daily concentration breakpoints for every report index.
    assessment = calculate(concentrations, period='day')
    terms = assessment['iaqi']
    total = sum(terms.values())
    value = assessment['aqi']
    return {'value': value, 'rank_value': value, 'rank': None, 'terms': terms,
            'ratios': {m: v / total for m, v in terms.items()} if total else None,
            'inputs': inputs, 'excluded': excluded, 'missing': assessment['missing'],
            'partial': assessment['partial'],
            'unavailable_reason': ('no_valid_concentrations' if not terms else
                'missing_indicators_and_max_iaqi_not_above_100') if value is None else None}


def evaluate(stats):
    end = stats['request']['end_date']
    result = {'version': VERSION, 'name': '综合指数', 'available': False,
              'selected_hangar_count': len(stats['hangars']), 'hangars': {}, 'gaps': [],
              'period_basis': 'hj633_daily_breakpoints_on_platform_period_concentrations',
              'composition_basis': 'valid_period_iaqi / sum_valid_period_iaqi',
              'qualified_daily_aqi': False, 'reference_state': '298.15K_101.325kPa',
              'ozone_basis': 'platform_period_mean_not_daily_maximum_8h'}
    for hid, hangar in stats['hangars'].items():
        records = {}
        for period in ('last_day', 'current', 'previous'):
            source = hangar['days'].get(end, {}) if period == 'last_day' else hangar[period]
            records[period] = period_record(hangar, source.get('survey', {}))
            if records[period]['value'] is None:
                result['gaps'].append({'hangar_id': hid, 'period': period,
                    'code': 'REPORT_INDEX_UNAVAILABLE', 'reason': records[period]['unavailable_reason'],
                    'message': '平台周期浓度或有效指标不足，未形成报告综合指数'})
        result['hangars'][hid] = records
    for period in ('last_day', 'current', 'previous'):
        values = [h[period]['rank_value'] for h in result['hangars'].values() if h[period]['rank_value'] is not None]
        for h in result['hangars'].values():
            value = h[period]['rank_value']
            h[period]['rank'] = 1 + sum(v < value for v in values) if value is not None else None
    result['available'] = any(h['current']['value'] is not None for h in result['hangars'].values())
    return result
