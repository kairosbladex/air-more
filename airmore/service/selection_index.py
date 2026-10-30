"""Six-pollutant composition relative to the selected current observations.

The same per-metric reference is frozen for both periods. Native units are
never mixed, missing values never become zero, and each physical hangar has
one reference vote regardless of how many selected sites it owns.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import math
from statistics import mean

from .models import METRICS

VERSION = 'selected-hangar-six-relative/v2'


def rounded(value):
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def competition_ranks(values):
    values = {key: rounded(value) for key, value in values.items() if value is not None}
    return {key: 1 + sum(other < value for other in values.values()) for key, value in values.items()}


def complete(survey, units=None):
    return all(isinstance(survey.get(m, {}).get('value'), (int, float))
               and math.isfinite(survey[m]['value']) and survey[m]['value'] >= 0
               and survey[m].get('unit')
               and (units is None or survey[m]['unit'] == units[m]) for m in METRICS)


def evaluate(stats):
    selected = stats['request']['hangar_ids']
    if not selected or set(selected) != set(stats['hangars']):
        raise ValueError('Index scope differs from selected sites')
    bindings = {b['id']: b for b in stats.get('provenance', {}).get('bindings', [])}
    groups = defaultdict(list)
    for site in selected:
        groups[bindings.get(site, {}).get('physical_hangar_id') or site].append(site)
    result = {'version': VERSION, 'name': '综合相对指数', 'formal_air_quality_composite': False,
              'selected_hangar_count': len(groups), 'groups': dict(groups),
              'reference_basis': 'selected_current_observations',
              'formula': 'J_m = 100/6 * C_m/B_m; B_m = equal-hangar mean of current complete six-metric observations; share_m = J_m/sum(J)',
              'rank_basis': 'Selected physical hangars only; ascending index; 2-decimal rounding then competition ties; missing values unranked',
              'hangars': {s: {p: None for p in ('current', 'previous', 'last_day')} for s in selected},
              'reference': {}, 'gaps': [], 'available': False}
    current = {s: stats['hangars'][s]['current'].get('survey', {}) for s in selected}
    valid = {s: v for s, v in current.items() if complete(v)}
    reference_groups = {g: ids for g, ids in groups.items() if all(s in valid for s in ids)}
    reference_sites = [s for ids in reference_groups.values() for s in ids]
    result['reference_hangar_ids'] = list(reference_groups)
    result['reference_hangar_count'] = len(reference_groups)
    for s in selected:
        if s not in reference_sites:
            result['gaps'].append({'site_id': s, 'period': 'current', 'code': 'SIX_MEASUREMENT_MISSING'})
    if not reference_sites:
        return result
    if any(len({valid[s][m]['unit'] for s in reference_sites}) != 1 for m in METRICS):
        result['gaps'].append({'code': 'SIX_UNIT_MISMATCH'})
        return result
    units = {m: valid[reference_sites[0]][m]['unit'] for m in METRICS}
    ref = {m: mean(mean(valid[s][m]['value'] for s in ids) for ids in reference_groups.values()) for m in METRICS}
    if any(v <= 0 or not math.isfinite(v) for v in ref.values()):
        result['gaps'].append({'code': 'SIX_CURRENT_REFERENCE_NONPOSITIVE'})
        return result
    result.update(reference=ref, units=units)
    end = stats['request']['end_date']
    for period in ('current', 'previous', 'last_day'):
        terms_by_site = {}
        for site in selected:
            h = stats['hangars'][site]
            source = h['days'].get(end, {}) if period == 'last_day' else h[period]
            values = source.get('survey', {})
            if not complete(values, units):
                gap = {'site_id': site, 'period': period, 'code': 'SIX_MEASUREMENT_MISSING'}
                if gap not in result['gaps']:
                    result['gaps'].append(gap)
                continue
            terms_by_site[site] = {m: 100 / 6 * values[m]['value'] / ref[m] for m in METRICS}
        grouped = {g: {m: mean(terms_by_site[s][m] for s in ids) for m in METRICS}
                   for g, ids in groups.items() if all(s in terms_by_site for s in ids)}
        scores = {g: sum(terms.values()) for g, terms in grouped.items()}
        ranks = competition_ranks(scores)
        for group, sites in groups.items():
            for site in sites:
                if site not in terms_by_site:
                    continue
                terms = terms_by_site[site]
                total = sum(terms.values())
                result['hangars'][site][period] = {
                    'value': total, 'terms': terms,
                    'ratios': {m: v / total for m, v in terms.items()} if total else None,
                    'rank': ranks.get(group), 'rank_value': scores.get(group), 'physical_hangar_id': group}
    result['available'] = any(h['current'] for h in result['hangars'].values())
    return result
