import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import pytest
from airmore.service import regional_context as rc
from airmore.service.models import ReportError


@pytest.mark.parametrize('legacy_flag', [None, '0', '1'])
def test_site_export_preserves_database_statistics(monkeypatch, tmp_path, legacy_flag):
    from airmore.service import render, approved_report
    called=[]
    monkeypatch.setattr(render, 'preflight', lambda cfg: {})
    monkeypatch.setattr(render, 'convert_pdf', lambda *args: None)
    if legacy_flag is None:
        monkeypatch.delenv('AIRMORE_REGIONAL_ENRICHMENT', raising=False)
    else:
        monkeypatch.setenv('AIRMORE_REGIONAL_ENRICHMENT', legacy_flag)
    stats = {'request': {'project_id': 'p'}, 'hangars': {'selected-site': {}}}
    analyses, composite = {'selected-site': object()}, {'eligible': False}
    def render_selected(actual_stats, actual_analyses, actual_composite, *args):
        assert actual_stats is stats
        assert actual_analyses is analyses
        assert actual_composite is composite
        called.append('site-document')
        return {'database': True}
    monkeypatch.setattr(approved_report, 'render_document', render_selected)
    monkeypatch.setattr(rc, 'load_context', lambda *args: called.append('city') or {'period':{'current':{'missing':[]}},'gaps':[]})
    from airmore.service import regional_report
    monkeypatch.setattr(regional_report,'make_advice',lambda *args:called.append('advice') or {})
    monkeypatch.setattr(regional_report,'render_document',lambda *args:called.append('document') or {'city':True})
    result=render.render(stats, analyses, composite, SimpleNamespace(), tmp_path)
    assert called == ['site-document']
    assert result == {'database':True}



def test_reference_city_aqi_and_pie_values_are_reproducible():
    raw=(Path(__file__).parent/'fixtures/regional/zhengzhou-history-20260918.json').read_bytes()
    rows=rc.observations(raw,'410100')
    latest=next(r for r in rows if r['date']=='2026-09-17')
    assert latest['aqi']==80
    assert round(latest['iaqi'][3]/sum(latest['iaqi'])*100,1)==47.9
    with pytest.raises(ValueError,match='scope'):
        rc.observations(raw,'640100')


def test_selected_window_is_not_replaced_with_latest_day():
    rows=[{'date':'2026-09-17','aqi':80,'values':[6,16,.5,135,30,19],'iaqi':[6,20,13,80,30,28],'primary':'臭氧'}]
    request={'start_date':'2026-09-18','end_date':'2026-09-18','report_type':'DAY'}
    with pytest.raises(ReportError,match='城市日监测'):
        rc.make_period(rows,request,date(2026,9,20))


def test_city_resolver_does_not_apply_project_city_to_outside_site():
    sites=[{'id':'a','name':'A','position':'','lng_lat_alt':json.dumps({'geometry':{'coordinates':[10,10]}})}]
    city=[{'CityCode':410100,'Area':'郑州市'}]
    boundary={'features':[{'geometry':{'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}}]}
    with pytest.raises(ReportError,match='不一致'):
        rc.resolve_sites(sites,city,{'city_code':'410100'},lambda *a:json.dumps(boundary))


def test_mixed_cities_cannot_be_merged():
    sites=[{'id':'a','name':'A','position':'郑州市','lng_lat_alt':None},
           {'id':'b','name':'B','position':'银川市','lng_lat_alt':None}]
    with pytest.raises(ReportError,match='跨城市'):
        rc.resolve_sites(sites,[{'CityCode':410100,'Area':'郑州市'},{'CityCode':640100,'Area':'银川市'}],{},None)


def test_day_keeps_selected_date_and_fourteen_day_context():
    rows=rc.observations((Path(__file__).parent/'fixtures/regional/zhengzhou-history-20260918.json').read_bytes(),'410100')
    p=rc.make_period(rows,{'start_date':'2026-09-17','end_date':'2026-09-17','report_type':'DAY'},date(2026,9,20))
    assert p['current']['days']==1 and p['current']['rows'][0]['aqi']==80
    assert p['trend']['days']==14 and p['trend_start']=='2026-09-04'
    assert p['as_of']=='2026-09-20'


def test_bad_published_aqi_cannot_be_used_for_pies():
    raw=json.loads((Path(__file__).parent/'fixtures/regional/zhengzhou-history-20260918.json').read_text())
    raw[0]['AQI']='1'
    with pytest.raises(ValueError,match='reconciliation'):
        rc.observations(json.dumps(raw),'410100')


def test_forecast_is_bound_to_city_and_issue_date():
    rows=[{'areaCode':'410100','dataDate':f'202609{d}','aqi_min':70,'aqi_max':110} for d in range(18,23)]
    assert len(rc.select_forecasts(rows,'410100',date(2026,9,18)))==5
    with pytest.raises(ValueError,match='stale'):
        rc.select_forecasts(rows,'410100',date(2026,9,20))
    with pytest.raises(ValueError,match='city'):
        rc.select_forecasts(rows,'640100',date(2026,9,18))


def test_city_document_has_actual_pies_aqi_and_detailed_advice(tmp_path):
    from airmore.service.regional_report import render_document
    from tests.test_product_report_contract import report_case, runtime, document_text
    rows=rc.observations((Path(__file__).parent/'fixtures/regional/zhengzhou-history-20260918.json').read_bytes(),'410100')
    p=rc.make_period(rows,{'start_date':'2026-09-17','end_date':'2026-09-17','report_type':'DAY'},date(2026,9,18))
    stats,_=report_case()
    context={'city_name':'郑州市','city_code':'410100','period':p,'monitor':None,'forecasts':[], 'weather':[],'gaps':[],
             'bindings':[{'site_id':k,'simulated':False} for k in stats['hangars']]}
    advice={'facts':rc.advice_facts(context,stats),'validation':'rules_and_independent_model_review_passed','gpu':{'size_vram':1},
        'recommendations':[{'evidence_id':ref,'title':title,'paragraph':prose} for ref,title,prose in [
        ('city','复核城市臭氧','值班人员核对城市臭氧变化，对照同条件复测记录，确认重复高值后再核查周边排放活动，并复核背景变化。'),
        ('particles','颗粒物背景对照','现场人员核对颗粒物采样条件，按相同位置和时段安排复测，同步保留背景对照，发现重复差异后核查周边活动记录。'),
        ('site','站点采样核查','设备维护人员核对采样单位及配置，复核站点定位与实际仪器状态，确认记录一致后再开展背景对照，不补填缺失记录。')]]}
    result=render_document(context,advice,stats,tmp_path,runtime())
    text=document_text(tmp_path/'report.docx')
    assert '47.9%' in text and 'AQI为80—80' in text
    assert '完整日评价资料不足' not in text and '没有满足完整日评价条件' not in text
    assert all(word not in text for word in ('所选站点巡测情况','模拟','联调','巡测','设备维护人员'))
    assert (tmp_path/'assets/pies.png').stat().st_size>15000
    assert result['regional']['period']['current']['iaqi_totals']==rows[-1]['iaqi']
    advice['facts']={}
    with pytest.raises(ValueError,match='stale'):
        render_document(context,advice,stats,tmp_path,runtime())


def test_city_editor_repairs_numeric_draft_before_independent_review():
    from airmore.service.grounded_advice import generate
    facts={key:'只允许依据已核验记录安排未来复核，不添加数值。' for key in ('city','particles','forecast','site')}
    draft={'recommendations':[{'evidence_id':key,'title':'核查相关记录','paragraph':'未来五日核查。'} for key in facts]}
    edited={'recommendations':[{'evidence_id':key,'title':'核查相关记录','paragraph':text} for key,text in zip(facts,[
        '值班人员核对城市臭氧变化，对照相近时段复测记录，确认重复高值后再核查周边排放活动，保留现场记录，并在背景变化时调整巡查次序。',
        '监测人员核对颗粒物采样条件，按相近位置和时段安排复测，同步保留背景对照，发现重复差异后再核查施工活动记录，避免把背景波动直接归因于具体来源。',
        '研判人员核对届时更新的正式预报和实况，记录预报偏差；若重点污染物出现变化，再调整后续观测安排，保留原始预报版本供会商复核。',
        '技术人员核查站点模拟记录和真实观测的区分，复核设备单位和实际位置，仅使用有原始记录支持的数据；若关联不一致，先核实后再纳入统计，不补填缺测。'])]}
    class Writer:
        cfg=SimpleNamespace(ollama_model='test-model')
        responses=iter([draft,edited,{'valid':True,'reason':'事实与周期一致'}])
        def _request(self,payload):
            return {'model':'test-model','done':True,'done_reason':'stop','message':{'content':json.dumps(next(self.responses),ensure_ascii=False)}}
        def runtime_probe(self):return {'size_vram':1}
    result=generate(Writer(),facts,'MONTH',city_mode=True)
    assert result['recommendations']==edited['recommendations']
    assert 'editorial_revision' in result['attempts'][0]


def test_review_plain_text_is_not_mistaken_for_success():
    from airmore.service.grounded_advice import review_response
    class Writer:
        cfg=SimpleNamespace(ollama_model='test-model')
        responses=iter(['valid。',json.dumps({'valid':False,'reason':'周期不符'})])
        def _request(self,payload):
            return {'model':'test-model','done':True,'done_reason':'stop','message':{'content':next(self.responses)}}
    result=review_response(Writer(),{'messages':[]})
    assert json.loads(result['message']['content'])['valid'] is False
    assert len(result['format_attempts'])==2
