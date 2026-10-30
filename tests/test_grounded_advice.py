"""Prevent plausible unsupported prose from entering delivered reports."""
import pytest

from airmore.service.grounded_advice import validate


def body():
    return {"recommendations": [
        {"evidence_id": "particles", "title": "保留高值时段对照", "paragraph": "观测人员在下一轮保留与本期相近的时段和高度，对照颗粒物高值记录与同步背景；若高值能够复现，再结合现场活动记录排查，按复测结果调整后续观测重点。"},
        {"evidence_id": "units", "title": "先核实气体量纲", "paragraph": "数据人员在本班核对传感器原始协议、量纲与缩放系数，并用原始记录比对转换结果；经确认后再更新展示口径，防止把原生数值直接用于污染等级判断。"},
    ]}


def test_original_actionable_prose():
    assert len(validate(body(), {"particles": "verified", "units": "verified"}, "DAY")) == 2


def test_complete_recommendations_with_only_outer_brace_missing():
    import json
    from airmore.service.grounded_advice import parse_advice
    original=json.dumps(body(),ensure_ascii=False)
    parsed,repair=parse_advice(original[:-1],allow_outer_brace=True)
    assert parsed==body() and repair=='closed_outer_object'
    assert parse_advice(original,allow_outer_brace=True)==(body(),None)
    for malformed in (original[:-2],original[:-8],original+' trailing',original.replace('recommendations','unknown')[:-1]):
        with pytest.raises(ValueError):parse_advice(malformed,allow_outer_brace=True)
    with pytest.raises(ValueError):parse_advice(original[:-1])


def test_advice_retry_identifies_rejected_phrase_and_discards_repeated_draft():
    """The real WEEK failure echoed the same instruction prose three times."""
    import json
    from types import SimpleNamespace
    from airmore.service.grounded_advice import generate
    facts = dict.fromkeys(('particles', 'comparison', 'forecast', 'handover'), '已有采样记录，现场情况待复核。')
    good = {'recommendations': [
        {'evidence_id': ref, 'title': '核对观测记录', 'paragraph':
         '下周，值班人员核对所选机库采样时段及原始记录，在相近位置和高度安排背景对照，保留现场复测资料；若结果与原记录不一致，再由设备维护人员复核仪器状态。'}
        for ref in facts]}
    bad = json.loads(json.dumps(good))
    bad['recommendations'][1]['paragraph'] += '不认定污染来源或治理成效。'

    class Writer:
        cfg = SimpleNamespace(ollama_model='test-model')
        calls = 0
        def _request(self, payload):
            self.calls += 1
            if self.calls == 1:
                answer = bad
            elif self.calls == 2:
                assert [m['role'] for m in payload['messages']] == ['system', 'user']
                feedback = payload['messages'][-1]['content']
                assert '治理成效' in feedback and '下周' in feedback
                assert bad['recommendations'][1]['paragraph'] not in feedback
                answer = good
            else:
                answer = {'valid': True, 'reason': '条件性工作建议与事实一致'}
            return {'model': self.cfg.ollama_model, 'done': True, 'done_reason': 'stop',
                    'message': {'content': json.dumps(answer, ensure_ascii=False)}}
        def runtime_probe(self):
            return {'size_vram': 1}

    result = generate(Writer(), facts, 'WEEK', recommendation_count=4, advice_only=True)
    assert len(result['attempts']) == 2
    assert result['validation'] == 'rules_and_independent_model_review_passed'


def test_report_advice_must_state_its_work_period():
    from airmore.service.grounded_advice import validate_report_advice
    with pytest.raises(ValueError, match='下周'):
        validate_report_advice(body()['recommendations'], 'WEEK')


@pytest.mark.parametrize('internal_word', ['联调记录', '测试补数', '模拟数据', '合成数据'])
def test_report_advice_keeps_internal_source_labels_out_of_business_prose(internal_word):
    from airmore.service.grounded_advice import validate_report_advice
    items = [{'paragraph': '本班，值班人员核对' + internal_word + '并保存设备原始记录。'}]
    with pytest.raises(ValueError, match='内部数据来源标记'):
        validate_report_advice(items, 'DAY')
    items[0]['paragraph'] = '本班，值班人员核对原始采样记录、仪器单位及校准状态，保存复核结果供交接使用。'
    validate_report_advice(items, 'DAY')


def test_future_period_advice_does_not_receive_current_short_range_weather():
    from airmore.service.approved_advice import facts_for
    stats={'request':{'report_type':'MONTH'},'hangars':{},'forecast':{
        'city_name':'郑州市','issue_date':'2026-09-20',
        'forecasts':[{'dataDate':'20260921'}],'weather':[{'description':'小雨转晴'}],'gaps':[]}}
    assert '小雨转晴' not in facts_for(stats)['forecast']
    assert '下月' in facts_for(stats)['forecast']
    stats['request']['report_type']='WEEK'
    assert '小雨转晴' not in facts_for(stats)['forecast']
    stats['request']['report_type']='DAY'
    assert '小雨转晴' in facts_for(stats)['forecast']


def test_advice_does_not_assert_unverified_abnormal_measurements():
    from airmore.service.grounded_advice import validate_report_advice
    items=body()['recommendations']
    items[0]['paragraph']='下月，研判人员分析数据异常波动原因，核对监测记录并安排复测。'
    with pytest.raises(ValueError, match='异常'):
        validate_report_advice(items, 'MONTH')
    items[0]['paragraph']='下月，若发现数据异常，研判人员核对监测记录并安排复测。'
    validate_report_advice(items, 'MONTH')


def test_digits_in_known_analyte_and_exact_source_identity_are_not_measurements():
    b = body()
    b['recommendations'][0]['paragraph'] += '请对照联调-灵嗅Nano2大气单兵的PM2.5原始记录。'
    facts = {'particles': '联调-灵嗅Nano2大气单兵本期PM2.5均值已核实。', 'units': 'verified'}
    assert validate(b, facts, 'DAY')
    b['recommendations'][0]['paragraph'] += '明天PM2.5为30。'
    with pytest.raises(ValueError):
        validate(b, facts, 'DAY')


def test_date_must_be_bound_to_the_cited_card():
    b = body()
    b['recommendations'][0]['paragraph'] += '9月22日请对照更新后的预报复核。'
    facts = {'particles': '9月22日有预报。', 'units': 'verified'}
    assert validate(b, facts, 'DAY')
    with pytest.raises(ValueError):
        validate(b, {'particles': 'verified', 'units': '9月22日有预报。'}, 'DAY')


@pytest.mark.parametrize("mutation", [
    lambda b: b["recommendations"][0].update(evidence_id="invented"),
    lambda b: b["recommendations"][1].update(evidence_id="particles"),
    lambda b: b["recommendations"][0].update(paragraph=body()["recommendations"][0]["paragraph"] + "明日AQI为80。"),
    lambda b: b["recommendations"][0].update(paragraph=body()["recommendations"][0]["paragraph"] + "污染源为道路扬尘。"),
    lambda b: b["recommendations"][0].update(paragraph=body()["recommendations"][0]["paragraph"].replace("下一轮", "下月")),
])
def test_reject_unknown_fact_duplicate_numeric_invention_source_claim_and_wrong_period(mutation):
    b = body()
    mutation(b)
    with pytest.raises(ValueError):
        validate(b, {"particles": "verified", "units": "verified"}, "DAY")


@pytest.mark.parametrize('leak', ['所有行动段均使用中文污染物名称且不抄字段名。', '研判人员在所选机库内按浓度由低到高排序。'])
def test_report_advice_rejects_editor_leak_and_wrong_rank_basis(leak):
    from airmore.service.grounded_advice import validate_report_advice
    with pytest.raises(ValueError):
        validate_report_advice([{'paragraph':'下周，值班人员核对原始记录并保存复核结果。'+leak}], 'WEEK')


@pytest.mark.parametrize('sentence', ['如条件不符需暂停相关指数排名计算。', '待记录核实后方可恢复常规数据上报流程。', '若发现仪器异常需立即暂停任务并上报。'])
def test_report_advice_keeps_existing_operational_process(sentence):
    from airmore.service.grounded_advice import validate_report_advice
    with pytest.raises(ValueError, match='业务中断'):
        validate_report_advice([{'paragraph':'本班及下一轮，值班人员核对原始记录。'+sentence}], 'DAY')



def test_advice_json_fence_preserves_body_and_rejects_partial_or_extra_text():
    import json
    from airmore.service.grounded_advice import parse_advice
    value={'recommendations':[{'evidence_id':'particles','title':'记录核对','paragraph':'待核对。'}]}
    raw=json.dumps(value,ensure_ascii=False)
    parsed,repair=parse_advice('```json\n'+raw+'\n```',allow_outer_brace=True)
    assert parsed==value and repair=='removed_json_fence'
    for malformed in ['preface\n```json\n'+raw+'\n```','```json\n'+raw+'\n```\nafterword','```json\n'+raw[:-10]+'\n```']:
        with pytest.raises(ValueError):parse_advice(malformed,allow_outer_brace=True)
    with pytest.raises(ValueError):parse_advice('```json\n'+raw+'\n```')



def test_next_observation_is_not_an_invented_quantity():
    b=body()
    b['recommendations'][0]['paragraph']+='将记录用于下一次观测安排。'
    assert validate(b,{'particles':'verified','units':'verified'},'DAY')
    for quantity in ('等待一小时后采样。','现场复测一次。'):
        bad=body();bad['recommendations'][0]['paragraph']+=quantity
        with pytest.raises(ValueError,match='事实数字'):
            validate(bad,{'particles':'verified','units':'verified'},'DAY')
