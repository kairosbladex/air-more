from copy import deepcopy
from datetime import date
import io

from docx import Document
from PIL import Image
import pytest

from airmore.service.models import METRICS, UNITS
from airmore.service.aqi import iaqi
from airmore.service.report_index import evaluate, mass_concentration, period_record
from tests.test_hangar_trend_alignment import case, record, statistics
from tests.test_report_aqi_alignment import config


def survey(**values):
    return {m: {'value': values.get(m, 0), 'unit': UNITS[m]} for m in METRICS}


@pytest.mark.parametrize('kind', ['DAY', 'WEEK', 'MONTH'])
def test_all_report_windows_use_daily_breakpoints_not_hourly(kind):
    req,data=case(kind,current=[record('2026-09-17' if kind=='DAY' else
                                       '2026-09-14' if kind=='WEEK' else '2026-09-01',
                                       so2=0,no2=100,co=0,o3=0,pm10=0,pm2_5=0)],raw=False)
    data.unit_configs=[config(m,UNITS[m]) for m in METRICS]
    index=evaluate(statistics(req,data))['hangars']['h']['current']
    assert index['inputs']['no2']['rounded_mass_value']==100
    assert index['terms']['no2']==iaqi('no2',100,period='day')==110
    assert iaqi('no2',100,period='hour')==50
    assert index['value']==110


def test_screenshot_concentrations_have_independent_hj633_results():
    req,data=case(current=[record(so2=.0089,no2=.0482,co=.3636,o3=.0789,
                                  pm10=25.6055,pm2_5=22.6491)],raw=False)
    stats=statistics(req,data)
    index=evaluate(stats)['hangars']['h']['current']
    # Reference-state conversion gives rounded 23, 91, .4, 155, 26, 23.
    # HJ633 table 3 interpolation and upward rounding: 23,106,10,96,26,33.
    assert [v['rounded_mass_value'] for v in index['inputs'].values()]==[23,91,.4,155,26,23]
    assert index['terms']==dict(zip(METRICS,[23,106,10,96,26,33]))
    assert index['value']==106 and index['rank']==1
    assert index['ratios']=={m:v/294 for m,v in index['terms'].items()}
    assert stats['hangars']['h']['days']['2026-09-17']['standard']['aqi'] is None


@pytest.mark.parametrize('metric,value,unit,expected', [
    ('co',4000,'μg/m³',4),('so2',.15,'mg/m³',150),('pm10',.12,'mg/m³',120),
    ('pm2_5',60,'ug/m3',60),('co',1,'ppm',1.144945692),
    ('no2',1000,'ppb',1880.719432),
])
def test_unit_conversion(metric,value,unit,expected):
    concentration,reason=mass_concentration(metric,value,unit)
    assert reason is None and float(concentration)==pytest.approx(expected,rel=1e-7)


@pytest.mark.parametrize('value,unit', [(None,'ppm'),(-1,'ppm'),('NaN','ppm'),
    (float('inf'),'ppm'),(True,'ppm'),(1,None),(1,'unknown')])
def test_invalid_inputs_are_not_zero_or_assumed_units(value,unit):
    assert mass_concentration('co',value,unit)[0] is None
    assert mass_concentration('pm10',1,'ppm')[1]=='unit_incompatible'


@pytest.mark.parametrize('kind', ['DAY','WEEK','MONTH'])
def test_period_summary_is_authoritative_and_last_day_stays_missing(kind):
    req,data=case(kind,current=[record('2026-09-17' if kind=='DAY' else '2026-09-14',pm2_5=60)],raw=False)
    data.unit_configs=[config(m,UNITS[m]) for m in METRICS]
    data.trend['daily']=[]
    stats=statistics(req,data)
    # Conflicting qualified daily AQI must neither replace the period summary
    # nor provide a value for the missing last day.
    for d in stats['hangars']['h']['days'].values():d['standard']={'aqi':500,'iaqi':{m:500 for m in METRICS}}
    result=evaluate(stats)['hangars']['h']
    assert result['current']['value']==100
    assert result['current']['terms']['pm2_5']==100
    assert result['last_day']['value'] is None and result['last_day']['ratios'] is None
    assert result['previous']['value'] is None


def test_missing_rule_retains_partial_pie_and_zero_denominator():
    low=period_record({},survey(so2=None,pm2_5=60))
    assert low['value'] is None and low['terms']['pm2_5']==100
    assert sum(low['ratios'].values())==1
    high=period_record({},survey(so2=None,pm2_5=61))
    assert high['value']==101
    zeros=period_record({},survey())
    assert zeros['value']==1 and zeros['ratios'] is None
    assert period_record({}, {})['value'] is None


def test_registered_unit_conflict_is_not_overridden_by_present_value():
    h={'indicator_validity':{m:{'valid':True,'unit':UNITS[m]} for m in METRICS}}
    cells=survey();cells['co']={'value':5,'unit':'ppm'}
    result=period_record(h,cells)
    assert 'co' not in result['terms'] and result['excluded']['co']=='unit_conflict'


def test_data_first_table_sort_tied_ranks_slashes_and_real_zero(tmp_path,monkeypatch):
    from airmore.service import approved_report as report
    from airmore.service.approved_advice import write_report
    from airmore.service.config import Settings
    req,data=case(current=[record()],raw=False)
    stats=statistics(req,data)
    proto=stats['hangars']['h']
    for pm25,key in [(None,'a-empty'),(60,'b'),(60,'c'),(61,'d'),(0,'e-zero')]:
        h=deepcopy(proto);h['name']=key;h.pop('indicator_validity',None)
        cells=survey(pm2_5=pm25)
        if pm25 is None:cells={m:{'value':None,'unit':UNITS[m]} for m in METRICS}
        h['current']['survey']=cells;h['days'][req.end_date.isoformat()]['survey']=cells
        stats['hangars'][key]=h
    del stats['hangars']['h']
    index=evaluate(stats)
    assert [index['hangars'][k]['current']['rank'] for k in stats['hangars']]==[None,2,2,4,1]
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
    for name in ('pie','forecast_card','measured_trend'):monkeypatch.setattr(report,name,lambda *args:image.getvalue())
    report.render_document(stats,write_report(None,stats),index,Settings(),tmp_path,{'font_family':'DejaVu Sans'})
    rows=Document(tmp_path/'report.docx').tables[1].rows
    assert [r.cells[0].text for r in rows[1:]]==['e-zero','b','c','d','a-empty']
    assert [c.text for c in rows[-1].cells[1:]]==['/']*8
    assert rows[1].cells[1].text=='0.00' and rows[1].cells[7].text=='1'
    # Removing the lower-valued zero row leaves the requested 1,1,3 ranking.
    del stats['hangars']['e-zero']
    assert [evaluate(stats)['hangars'][k]['current']['rank'] for k in ('b','c','d')]==[1,1,3]


def test_trend_is_one_axis_with_all_native_series_and_display_zeroes(monkeypatch):
    from airmore.service import approved_report as report
    captured=[]
    monkeypatch.setattr(report.st,'png',lambda fig:captured.append(fig))
    axis={'labels':['2026-09-18','2026-09-19','2026-09-20'],'series':[
        {'metric':'pm10','name':'PM10','unit':'μg/m³','values':[10,None,0]},
        {'metric':'co','name':'CO','unit':'ppm','values':[.2,.3,.4]},
        {'metric':'ch4','name':'CH₄','unit':None,'values':[None,None,None]}]}
    before=deepcopy(axis)
    report.measured_trend({'platform_trend':axis},{'font_family':'DejaVu Sans'})
    fig=captured[0];assert len(fig.axes)==1
    ax=fig.axes[0];assert len(ax.lines)==3 and len(ax.collections)==3
    assert [t.get_text() for t in ax.get_legend().get_texts()]==['PM10','CO','CH4']
    assert list(ax.lines[0].get_ydata())==[10,0,0]
    assert list(ax.lines[1].get_ydata())==[.2,.3,.4]
    assert list(ax.lines[2].get_ydata())==[0,0,0]
    assert axis==before
    report.plt.close(fig)
    axis['series'][0]['values']=[-1,None,0]
    axis['series'] += [{'metric':m,'name':m.upper(),'unit':'ppm','values':[None]*3}
                       for m in ('so2','no2','o3','ox','pm1_0','pm2_5')]
    report.measured_trend({'platform_trend':axis},{'font_family':'DejaVu Sans'})
    fig=captured[-1];ax=fig.axes[0]
    assert ax.get_ylim()[0]<0  # The platform can retain negative observations.
    fig.canvas.draw()
    labels=ax.get_legend().get_texts()
    visible_order=sorted(labels,key=lambda t:(-round(t.get_window_extent().y0),t.get_window_extent().x0))
    assert [t.get_text() for t in visible_order]==['PM10','CO','CH4','SO2','NO2','O3','OX','PM1_0','PM2_5']
    report.plt.close(fig)


@pytest.mark.parametrize('kind,count', [('DAY',15),('WEEK',8),('MONTH',12)])
def test_trend_draws_every_period_node_without_changing_statistics(kind,count,monkeypatch):
    from airmore.service import approved_report as report
    from airmore.service.hangar_trend import window
    req,data=case(kind,current=[record()],raw=False)
    stats=statistics(req,data)
    axis=window(kind,date(2026,9,20))
    values=[None]*count
    values[1],values[-2]=47.1234,25.6055
    axis['series']=[{'metric':'pm10','name':'PM10','unit':'μg/m³','values':values}]
    stats['hangars']['h']['platform_trend']=axis
    before=deepcopy(stats)
    indices=evaluate(stats)
    captured=[]
    monkeypatch.setattr(report.st,'png',lambda fig:captured.append(fig))
    report.measured_trend(stats['hangars']['h'],{'font_family':'DejaVu Sans'})
    fig=captured[0]
    line=fig.axes[0].lines[0]
    assert list(line.get_xdata())==list(range(count))
    assert list(line.get_ydata())==[v if v is not None else 0 for v in values]
    assert len(line.get_path().vertices)==len(axis['keys'])==count
    assert stats==before and evaluate(stats)==indices
    report.plt.close(fig)
