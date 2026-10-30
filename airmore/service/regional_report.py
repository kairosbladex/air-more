"""The reference's full city-fact workflow, integrated with service evidence."""
from datetime import date, timedelta
import copy
import json
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from airmore.regional.product_style import (new_document, paragraph, heading, table,
    image, page_break, TEMPLATE_SHA256, TEMPLATE_VERSION)
from airmore.regional.product_render import make_charts, date_text, date_range
from .product_report import _quality
from .regional_context import advice_facts
from airmore.regional.product_periods import digest
from .grounded_advice import generate, validate
from .writer import OllamaAnalysisWriter

VERSION = TEMPLATE_VERSION + '+city-service/v3'
ADVICE_VERSION = TEMPLATE_VERSION + '+city-service/v2'


def make_advice(context, stats, cfg):
    writer=OllamaAnalysisWriter(cfg)
    # Same GPU model used for the approved reference report; isolated writer
    # configuration avoids modifying the worker's device analysis settings.
    writer.cfg=copy.copy(cfg)
    writer.cfg.ollama_model='qwen3.6:latest'
    writer.cfg.model_timeout=300
    facts=advice_facts(context,stats)
    kind=context['period']['kind']
    key=digest({'facts':facts,'kind':kind,'model':writer.cfg.ollama_model,'version':ADVICE_VERSION})
    cache=Path(cfg.state_dir)/'regional-advice'
    cache.mkdir(parents=True,exist_ok=True)
    target=cache/(key+'.json')
    if target.exists():
        saved=json.loads(target.read_text())
        validate({'recommendations':saved['recommendations']},facts,kind)
        if (saved.get('facts')==facts and saved.get('validation')=='rules_and_independent_model_review_passed'
                and len(saved['recommendations'])>=3 and saved.get('gpu',{}).get('size_vram',0)>0):
            return dict(saved,cache_reused_for_identical_facts=True)
    try:
        advice=generate(writer,facts,kind,city_mode=True)
    except Exception:
        diagnostics=Path(cfg.state_dir)/'diagnostics';diagnostics.mkdir(parents=True,exist_ok=True)
        (diagnostics/('city-advice-'+key+'.json')).write_text(json.dumps(
            {'facts':facts,'attempts':getattr(writer,'advice_attempts',[]),
             'calls':getattr(writer,'call_audit',[])},ensure_ascii=False))
        raise
    if len(advice['recommendations']) < 3 or not advice.get('gpu',{}).get('size_vram',0):
        from .models import ReportError
        raise ReportError('MODEL_VALIDATION_FAILED','详细工作建议未满足完整性或GPU核验要求')
    temp=cache/(key+'.tmp');temp.write_text(json.dumps(advice,ensure_ascii=False));temp.replace(target)
    return advice


def render_document(context, advice, stats, output, runtime):
    facts=advice_facts(context,stats)
    if (advice.get('facts') != facts or advice.get('validation') != 'rules_and_independent_model_review_passed'
            or not advice.get('gpu',{}).get('size_vram',0)):
        raise ValueError('regional advice is stale or unverified')
    validate({'recommendations':advice['recommendations']},facts,context['period']['kind'])
    output=Path(output);assets=output/'assets';assets.mkdir(exist_ok=True)
    p=context['period'];s=p['current'];region=context['city_name'];trend=p['trend']
    chart_period=dict(p,current=trend,start=p['trend_start'])
    # Daily report keeps the reference's current-day versus recent peak-day pies.
    if p['kind']=='DAY':
        chart_period=dict(chart_period,current=dict(trend,iaqi_totals=s['iaqi_totals']))
    make_charts(chart_period,assets,runtime)
    doc=new_document(region,font_name=runtime.get('docx_font_family'))
    for _ in range(5):paragraph(doc,'',space=12)
    paragraph(doc,'空气质量研判'+p['name'],36,align=WD_ALIGN_PARAGRAPH.CENTER,space=18)
    paragraph(doc,region+'  '+date_range(p['start'],p['end']),14,align=WD_ALIGN_PARAGRAPH.CENTER,space=12)
    paragraph(doc,'编制日期：'+p['as_of'],10.5,align=WD_ALIGN_PARAGRAPH.CENTER)
    page_break(doc);heading(doc,'空气质量情况')
    summary=f"{date_range(p['start'],p['end'])}，{region}纳入统计{s['days']}个有效日，AQI为{s['aqi_min']}—{s['aqi_max']}，优良{s['good_days']}天，占有效天数的{s['good_percent']:.1f}%。"
    summary+=f"最高日为{date_text(s['peak']['date'])}，AQI为{s['peak']['aqi']}，首要污染物为{s['peak']['primary']}。"
    if s['missing']:summary+='本期缺测'+str(len(s['missing']))+'天，期间统计仅计有效日。'
    if p['comparable']:
        prior=p['previous'];diff=s['means'][3]-prior['means'][3]
        summary+=f"与前一等长时段相比，臭氧日最大8小时浓度平均值{'增加' if diff>=0 else '减少'}{abs(diff):.1f} μg/m³。"
    paragraph(doc,summary)
    heading(doc,'（一）城市空气质量情况表',True)
    entries=[['本期日值平均']+[f'{v:.2f}' if i==2 else f'{v:.1f}' for i,v in enumerate(s['means'])],
             ['本期日值最高']+[f'{max(r["values"][i] for r in s["rows"]):g}' for i in range(6)],
             [date_text(s['rows'][-1]['date'])]+[f'{v:g}' for v in s['rows'][-1]['values']]]
    if p['kind']=='DAY':
        entries=[[date_text(r['date'])]+[f'{v:g}' for v in r['values']]
                 for r in p['previous']['rows'][-1:]+s['rows']]
    table(doc,['统计项目']+list(('SO₂','NO₂','CO','O₃','PM₁₀','PM₂.₅')),entries,[2.6]+[2]*6,9)
    paragraph(doc,'CO单位为mg/m³，其余为μg/m³。臭氧为日最大8小时浓度，其余为日均浓度。',8.5)
    monitor=context.get('monitor')
    if monitor:
        paragraph(doc,monitor['data'][0]+' 城市监测站实况',9,align=WD_ALIGN_PARAGRAPH.CENTER)
        rows=[]
        for key,v in sorted(monitor['data'][1].items(),key=lambda kv:float(kv[1].get('O3',0)),reverse=True):
            values=[]
            for field in ('SO2','NO2','CO','O3','PM10','PM25','AQI'):
                value=v.get(field)
                values.append('待核' if value is None or float(value)<=0 else f'{float(value):g}')
            rows.append([monitor['stations'].get(key,key),*values])
        table(doc,['监测站','SO₂','NO₂','CO','O₃','PM₁₀','PM₂.₅','AQI'],rows,[3.05]+[1.65]*7,8)
        paragraph(doc,'实况为编制时最新时次，不混入报告统计期；按臭氧小时浓度排列，不代表日评价排名。待核值不按零值使用。',8)
    else:
        paragraph(doc,'本期逐日城市空气质量',9)
        rows=[[date_text(r['date']),r['aqi'],_quality(r['aqi']),r['primary']] for r in s['rows'][-10:]]
        table(doc,['日期','AQI','空气质量','首要污染物'],rows,[2.5,2,3,7],8.5)
    page_break(doc);heading(doc,'（二）六项污染物分指数占比分析',True)
    peak=trend['peak'];share=s['shares'][3]
    paragraph(doc,f"{region}本期臭氧分指数占六项累计分指数的{share:.1f}%。近期最高日为{date_text(peak['date'])}，AQI为{peak['aqi']}，首要污染物为{peak['primary']}。双图分别展示本期和近期最高日的分指数构成。")
    image(doc,assets/'pies.png')
    table(doc,['统计范围','SO₂','NO₂','CO','O₃','PM₁₀','PM₂.₅'],
          [['本期累计']+s['iaqi_totals'],[date_text(peak['date'])]+peak['iaqi']],[2.6]+[2]*6,9)
    paragraph(doc,'各项占比为该污染物IAQI占六项IAQI之和的比例；AQI取最大分指数。图示是指标构成，不是污染源贡献率。',8.5)
    heading(doc,'近期空气质量变化',True);image(doc,assets/'aqi.png')
    paragraph(doc,f"{date_range(p['trend_start'],p['end'])}共有{trend['days']}个有效日，AQI最低{trend['aqi_min']}、最高{trend['aqi_max']}，优良{trend['good_days']}天。最后一个有效日为{date_text(trend['rows'][-1]['date'])}，AQI为{trend['rows'][-1]['aqi']}。")
    page_break(doc);heading(doc,'城市六项指标变化趋势分析')
    paragraph(doc,f"{region}{date_range(p['trend_start'],p['end'])}六项污染物日浓度变化如下。各图使用独立纵轴，缺测日期断线，不插值。")
    image(doc,assets/'trends.png')
    a,b=trend['rows'][0],trend['rows'][-1]
    paragraph(doc,f"该时段臭氧日最大8小时浓度从{a['values'][3]:g}变为{b['values'][3]:g} μg/m³；PM₁₀从{a['values'][4]:g}变为{b['values'][4]:g} μg/m³，PM₂.₅从{a['values'][5]:g}变为{b['values'][5]:g} μg/m³。变化原因需结合气象、排放活动及现场复核判断。")
    page_break(doc);heading(doc,'环境空气质量预测');heading(doc,'（一）空气质量形势预报',True)
    fs=context['forecasts'];weather={w['date']:w for w in context['weather']}
    paragraph(doc,'以下为编制日'+p['as_of']+'起的城市预报，区别于前述历史统计期。',8.5)
    for f in fs[:3]:
        day=f['dataDate'][:4]+'-'+f['dataDate'][4:6]+'-'+f['dataDate'][6:]
        w=weather.get(day);text=date_text(day)+'，'
        if w:
            text+=w['description']+'，'
            if w['high'] is not None:text+=f"气温{str(w['low']).replace('℃','')}—{str(w['high']).replace('℃','')}℃，"
            text+=w['wind']+'，风力'+w['force']+'。'
        text+=f"预计城市AQI为{f['aqi_min']}—{f['aqi_max']}，空气质量为{_quality(f['aqi_min'])}至{_quality(f['aqi_max'])}，首要污染物为"+f['primaryPol'].replace('O3','臭氧').replace('PM2.5','细颗粒物').replace(',','、')+'。'
        paragraph(doc,text)
    if fs:
        risk=[f for f in fs if f['aqi_max']>100]
        paragraph(doc,('预报中有'+str(len(risk))+'天的AQI上限超过100，应结合每日更新预报和同条件复测安排核查。' if risk else '预报区间上限均未超过100，仍需对照实际观测更新研判。')+'预报不代表实测结果，不据此认定具体污染来源。')
        heading(doc,'（二）未来五天AQI数值预报',True)
        table(doc,[f['dataDate'][4:6]+'月'+f['dataDate'][6:]+'日' for f in fs],
              [[f"{f['aqi_min']}—{f['aqi_max']}" for f in fs],
               [_quality(f['aqi_min'])+'—'+_quality(f['aqi_max']) for f in fs],
               [f['primaryPol'].replace('O3','O₃').replace('PM2.5','PM₂.₅').replace(',','／') for f in fs],
               [weather.get(f['dataDate'][:4]+'-'+f['dataDate'][4:6]+'-'+f['dataDate'][6:],{}).get('description','待更新') for f in fs]], [14.6/len(fs)]*len(fs),9)
    else:paragraph(doc,'该城市当前没有取得与编制日对应的正式预报资料，本次不作未来AQI数值判断。')
    page_break(doc);heading(doc,'工作建议')
    paragraph(doc,{'DAY':'本班及下一轮以城市重点污染物、同条件背景对照和现场复核为重点。','WEEK':'下周安排以本周污染物变化、城市背景对照及现场复核为依据，近期预报仅供滚动调整参考。','MONTH':'下月观测与月度会商以本期有效日统计、持续高值复核和数据完整性为重点，近期预报不外推为下月趋势。'}[p['kind']])
    for item in advice['recommendations']:
        # Device provenance remains in evidence, outside the city-only document.
        if item['evidence_id'] == 'site':
            continue
        if any(word in item['paragraph'] for word in ('模拟', '联调', '巡测')):
            raise ValueError('city advice contains device-only content')
        para=paragraph(doc,item['paragraph'],10.5,space=13);para.paragraph_format.first_line_indent=Pt(21)
    if p['current']['missing']:
        paragraph(doc,'城市统计缺测日期：'+'、'.join(p['current']['missing'])+'。',8.5)
    for gap in context['gaps']:paragraph(doc,gap.split('：')[0]+'。',8.5)
    doc.save(output/'report.docx')
    return {'template_version':VERSION,'template_sha256':TEMPLATE_SHA256,
            'regional':context,'regional_advice':advice,'layout':'user-approved-product-format',
            'runtime':{k:v for k,v in runtime.items() if k!='env'}}
