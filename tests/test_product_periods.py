from datetime import date, timedelta
import pytest
from airmore.regional.product_periods import window, build_period, summarize
from airmore.regional.product_advice import validate


def observation(day, aqi=50, pm=20):
    return {'date':day.isoformat(),'values':[5,10,.5,90,30,pm],
            'iaqi':[5,13,13,45,30,29],'aqi':aqi,'primary':'臭氧8小时(O3_8h)'}


@pytest.mark.parametrize('as_of,week,month',[
    ('2026-09-18',('2026-09-07','2026-09-13'),('2026-09-01','2026-09-17')),
    ('2026-09-14',('2026-09-07','2026-09-13'),('2026-09-01','2026-09-13')),
    ('2027-01-01',('2026-12-21','2026-12-27'),('2026-12-01','2026-12-31')),
    ('2028-03-01',('2028-02-21','2028-02-27'),('2028-02-01','2028-02-29')),
])
def test_calendar_windows(as_of,week,month):
    d=date.fromisoformat(as_of)
    assert tuple(x.isoformat() for x in window('WEEK',d))==week
    assert tuple(x.isoformat() for x in window('MONTH',d))==month


def test_partial_month_keeps_gaps_out_of_denominator_and_refuses_comparison():
    rows=[observation(date(2026,9,d),aqi=120 if d<8 else 50) for d in range(4,18)]
    result=build_period(rows,'MONTH',date(2026,9,18))
    current=result['current']
    assert current['missing']==['2026-09-01','2026-09-02','2026-09-03']
    assert current['days']==14 and current['expected_days']==17
    assert current['good_percent']==71.4 and current['means'][-1]==20
    assert not result['comparable']


def test_previous_week_requires_complete_matching_coverage():
    start=date(2026,8,31)
    rows=[observation(start+timedelta(days=i),pm=10+i) for i in range(14)]
    result=build_period(rows,'WEEK',date(2026,9,18))
    assert result['comparable']
    assert result['current']['means'][-1]==20
    assert result['previous']['means'][-1]==13
    rows.pop(0)
    assert not build_period(rows,'WEEK',date(2026,9,18))['comparable']


def test_period_selection_excludes_future_and_foreign_days():
    rows=[observation(date(2026,9,d),pm=d) for d in range(1,25)]
    p=build_period(rows,'WEEK',date(2026,9,18))
    assert [r['date'] for r in p['current']['rows']]==[f'2026-09-{d:02}' for d in range(7,14)]
    changed=rows+[observation(date(2026,10,1),pm=999)]
    assert p['facts_sha256']==build_period(changed,'WEEK',date(2026,9,18))['facts_sha256']
    rows[6]['values'][-1]=999
    assert p['facts_sha256']!=build_period(rows,'WEEK',date(2026,9,18))['facts_sha256']


def test_no_data_is_an_error_not_a_zero_report():
    with pytest.raises(ValueError,match='no observations'):
        build_period([],'MONTH',date(2026,9,18))


@pytest.mark.parametrize('bad',['若背景同步升高则判定为区域传输影响。','本期没有降雨。','安排补录缺失的观测值。','下月开展复测。'])
def test_reject_unjustified_causality_weather_invented_backfill_or_wrong_horizon(bad):
    base='监测人员下周选择相近时段开展复测，同时核对背景站点与现场作业记录，若发现相对高值重复出现，再安排针对性排查并保留复核记录。'
    other='数据人员核对原始观测记录与有效性标记，对于存在疑问的记录先复核统计口径，核实后纳入分析，没有原始记录的日期继续保留缺测状态。'
    third='调度人员结合复测结果安排下一轮工作，明确各点位的记录责任与核查时间，并在执行后对照原计划复核完成情况，发现偏差后及时调整安排。'
    with pytest.raises(ValueError):validate({'paragraphs':[base+bad,other,third]},'WEEK')


def test_history_snapshots_merge_without_double_counting_and_reject_conflicts(tmp_path):
    import json,hashlib
    from datetime import datetime,timezone
    from airmore.regional.product_periods import load_observations
    def row(day,pm=20):
        return {'CityCode':410100,'TimePoint':f'/Date({int(datetime(2026,9,day,tzinfo=timezone(timedelta(hours=8))).timestamp()*1000)})/',
                'SO2_24h':5,'NO2_24h':10,'CO_24h':.5,'O3_8h_24h':90,
                'PM10_24h':30,'PM2_5_24h':pm,'AQI':45,'PrimaryPollutant':'O3'}
    files={'zhengzhou-history.json':[row(4),row(5)],'earlier-history.json':[row(3),row(4)]}
    def save():
        metadata=[]
        for name,rows in files.items():
            p=tmp_path/name;p.write_text(json.dumps(rows))
            metadata.append({'file':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'role':'city-day-history'})
        (tmp_path/'sources.json').write_text(json.dumps(metadata))
    save()
    assert len(load_observations(tmp_path))==3
    files['earlier-history.json'][1]=row(4,pm=21);save()
    with pytest.raises(ValueError,match='conflicting history'):
        load_observations(tmp_path)
    files['earlier-history.json'][1]=row(4);save()
    (tmp_path/'earlier-history.json').write_text('[]')
    with pytest.raises(ValueError,match='snapshot changed'):
        load_observations(tmp_path)


def test_recommendation_horizon_uses_issue_date_not_historical_period_end():
    from airmore.regional.product_periods import advice_facts
    rows=[observation(date(2026,9,d)) for d in range(4,18)]
    week=advice_facts(build_period(rows,'WEEK',date(2026,9,18)))
    month=advice_facts(build_period(rows,'MONTH',date(2026,9,18)))
    assert '2026-09-21至2026-09-27' in week['action_scope']
    assert '2026年10月' in month['action_scope']
