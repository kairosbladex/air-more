"""Match registered atmospheric payloads without multiplying sampling facts."""
from collections import Counter, defaultdict
from .models import ReportError


def select_atmospheric_samples(samples, bindings, payloads, allowed_types):
    by_device = defaultdict(list)
    for payload in payloads:
        if payload.get('load_imei'):
            by_device[(payload['uav_id'], payload['load_imei'])].append(payload)
    sites = {b['id']: b for b in bindings}
    selected, reasons, used = [], Counter(), {}
    for row in samples:
        site = sites[row['hangar_id']]
        candidates = by_device[(site['uav_id'], row.get('load_sn'))]
        if len(candidates) > 1:
            raise ReportError('AMBIGUOUS_PAYLOAD', '同一无人机采样编号对应多个载荷，需核实登记关系')
        if not candidates:
            reasons['payload_not_registered_to_uav'] += 1
            continue
        payload = candidates[0]
        if payload['load_type'] not in allowed_types:
            reasons['not_atmospheric_payload'] += 1
            continue
        # Some production devices publish the registered product code instead
        # of its category. Accept only this exact registration, never a prefix.
        accepted_codes = {payload['load_type']}
        if payload.get('load_brand_code'):
            accepted_codes.add(payload['load_brand_code'])
        if row.get('load_type') not in accepted_codes:
            reasons['payload_type_mismatch'] += 1
            continue
        # Measurements may carry the category (1003), while units are keyed by
        # the registered product. Resolve only through this exact UAV/serial
        # binding; never infer a product from a category prefix.
        if row['load_type'] == payload['load_type'] and payload.get('load_brand_code'):
            row = row | {'unit_product_num': payload['load_brand_code']}
        selected.append(row)
        used[payload['id']] = {k:payload.get(k) for k in
                              ('id','uav_id','load_name','load_type','load_type_name','load_brand_code','load_brand_name','load_model','state')}
    return selected, dict(reasons), list(used.values())
