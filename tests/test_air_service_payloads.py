import io
import json
import pytest
from airmore.service.payloads import select_atmospheric_samples
from airmore.service.models import ReportError
from airmore.service.statistics import configured_units, measurement
from airmore.service.indicators import fetch_indicators

BINDINGS=[{'id':'s','uav_id':'u'}]
ROW={'id':'a','hangar_id':'s','load_sn':'sn','load_type':'1003'}
PAYLOAD={'id':'l','uav_id':'u','load_imei':'sn','load_type':'1003','load_name':'大气传感器'}


def test_sensor_selection_by_bound_uav_and_exact_serial():
    rows=[ROW,ROW|{'id':'b','load_sn':'camera'},ROW|{'id':'c','load_sn':'missing'},ROW|{'id':'d','load_type':'bad'}]
    loads=[PAYLOAD,PAYLOAD|{'id':'c','load_imei':'camera','load_type':'1001'},PAYLOAD|{'id':'x','uav_id':'other'}]
    selected,reasons,used=select_atmospheric_samples(rows,BINDINGS,loads,('1003',))
    assert selected==[ROW]
    assert reasons=={'not_atmospheric_payload':1,'payload_not_registered_to_uav':1,'payload_type_mismatch':1}
    assert [v['id'] for v in used]==['l']
    assert select_atmospheric_samples(rows,BINDINGS,loads,('1003',))==(selected,reasons,used)


def test_multiple_sensors_no_join_multiplication_and_ambiguous_serial_rejected():
    rows=[ROW,ROW|{'id':'b','load_sn':'second'}]
    loads=[PAYLOAD,PAYLOAD|{'id':'b','load_imei':'second'}]
    assert len(select_atmospheric_samples(rows,BINDINGS,loads,('1003',))[0])==2
    with pytest.raises(ReportError) as e:
        select_atmospheric_samples([ROW],BINDINGS,[PAYLOAD,PAYLOAD|{'id':'duplicate'}],('1003',))
    assert e.value.code=='AMBIGUOUS_PAYLOAD'


def test_exact_registered_product_code_is_accepted_without_prefix_inference():
    payload = PAYLOAD | {'load_brand_code':'100301'}
    product = ROW | {'load_type':'100301'}
    wrong = ROW | {'id':'wrong','load_type':'100302'}
    selected, reasons, used = select_atmospheric_samples([product,wrong], BINDINGS, [payload], ('1003',))
    assert selected == [product]
    assert reasons == {'payload_type_mismatch':1}
    assert used[0]['load_brand_code'] == '100301'
    assert not select_atmospheric_samples([product], BINDINGS, [PAYLOAD], ('1003',))[0]
    assert not select_atmospheric_samples([product], BINDINGS, [payload | {'load_type':'1001'}], ('1003',))[0]


def units(entries):
    return configured_units([{'index_name':'co','index_unit':json.dumps(entries)}])


def test_project_default_unit_and_real_zero_are_retained_without_aqi():
    result=measurement({'load_type':'1003','co':0},'co',{},units([{'defaultUnit':'mg/m³'}]))
    assert result==(0,'mg/m³',None,'measurement_state_unconfirmed')


@pytest.mark.parametrize('entries', [
    [{'defaultUnit':'mg/m³'},{'defaultUnit':'ppm'}],
    [{'defaultUnit':'mg/m³'},{'productNum':'1003','defaultUnit':'ppm'},{'productNum':'1003','defaultUnit':'mg/m³'}],
])
def test_unit_conflicts_not_hidden_by_default(entries):
    assert measurement({'load_type':'1003','co':1},'co',{},units(entries))[3]=='unit_conflict'


def test_explicit_product_unit_precedes_default_but_blank_does_not_fall_back():
    entries=[{'defaultUnit':'mg/m³'},{'productNum':'1003','defaultUnit':'ppm'}]
    assert measurement({'load_type':'1003','co':1},'co',{},units(entries))[3]=='measurement_state_unconfirmed'
    entries[1]['defaultUnit']=''
    assert measurement({'load_type':'1003','co':1},'co',{},units(entries))[3]=='unit_unknown'


def test_short_indicators_path_is_configurable_without_moving_host():
    def opener(req,timeout):
        assert req.full_url=='https://service.test/api/screen/indicators'
        assert req.get_header('Projectid')=='p'
        return io.BytesIO(b'[]')
    assert fetch_indicators('p',{'p':{'base_url':'https://service.test/api','path':'/screen/indicators'}},opener)==[]
    with pytest.raises(ReportError):
        fetch_indicators('p',{'p':{'base_url':'https://service.test','path':'//other.test/screen/indicators'}},opener)


@pytest.mark.parametrize('metric', ['co', 'so2', 'no2', 'o3', 'pm10', 'pm2_5'])
def test_mass_units_normalized_without_claiming_aqi_eligibility(metric):
    for source, raw in [('mg/m³', 1.5), ('μg/m³', 1500)]:
        result = measurement({'load_type':'type', metric:raw}, metric, {}, {('type', metric):source})
        assert result == (1.5 if metric == 'co' else 1500, 'mg/m³' if metric == 'co' else 'μg/m³', None, 'measurement_state_unconfirmed')
