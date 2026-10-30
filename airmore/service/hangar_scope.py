"""Map selected site records to verified physical hangars before aggregation."""
from copy import deepcopy
from dataclasses import replace

from .models import ReportError


def group_selected(request, dataset):
    bindings = dataset.provenance.get('bindings', [])
    if not bindings and dataset.data_kind == 'synthetic':
        return request, dataset
    by_site = {b['id']: b for b in bindings}
    if set(by_site) != set(request.hangar_ids):
        raise ReportError('INVALID_DEVICE_LINKAGE', '所选站点缺少完整的机库关联')
    names, sites = {}, {}
    for site in request.hangar_ids:
        b = by_site[site]
        hid, name = b.get('physical_hangar_id'), b.get('physical_hangar_name')
        if not hid or not name or (hid in names and names[hid] != name):
            raise ReportError('INVALID_DEVICE_LINKAGE', '机库名称或关联不完整')
        names[hid] = name
        sites.setdefault(hid, []).append(site)
    rows = [row | {'source_site_id': row['hangar_id'], 'hangar_id': by_site[row['hangar_id']]['physical_hangar_id']}
            for row in dataset.samples]
    provenance = deepcopy(dataset.provenance)
    provenance.update(selected_site_ids=list(request.hangar_ids), site_bindings=bindings,
                      bindings=[{'id': hid, 'physical_hangar_id': hid, 'physical_hangar_name': names[hid],
                                 'name': names[hid], 'site_ids': sites[hid]} for hid in sorted(names)],
                      aggregation_scope='Selected sites grouped by physical bo_uav_hangar.id before minute/day aggregation')
    scoped = request.model_copy(update={'hangar_ids': sorted(names)})
    return scoped, replace(dataset, samples=rows, hangars={hid:names[hid] for hid in sorted(names)}, provenance=provenance)
