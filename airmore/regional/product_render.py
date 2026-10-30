"""Render DAY/WEEK/MONTH with a single approved product layout."""
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import os
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from lxml import html
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from .product_periods import LABELS, require, advice_facts
from .product_style import (TEMPLATE, TEMPLATE_SHA256, TEMPLATE_VERSION,
                             new_document, paragraph, heading, table, image, page_break)
from .product_advice import validate as validate_advice

COLORS = ('#5B9BD5','#ED7D31','#70AD47','#4472C4','#A5A5A5','#FFC000')
RENDERER_VERSION = 'regional/v1'


def date_text(value):
    d = date.fromisoformat(value)
    return f'{d.month}月{d.day}日'


def date_range(start, end):
    a,b = date.fromisoformat(start),date.fromisoformat(end)
    return f'{a.year}年{a.month}月{a.day}日—{b.month}月{b.day}日'


def forecasts(root, as_of):
    metadata=json.loads((root/'sources.json').read_text())
    weather_source=next((s for s in metadata if s.get('file')=='weather.html'),None)
    require(weather_source is not None, 'weather snapshot metadata missing')
    saved=weather_source.get('retrieved_at',weather_source.get('saved_at',''))
    require(bool(saved), 'weather snapshot time missing')
    saved_day=datetime.fromisoformat(saved).astimezone(ZoneInfo('Asia/Shanghai')).date()
    require(saved_day==as_of, 'weather snapshot does not match issue date')
    all_rows = json.loads((root/'forecast.json').read_text())
    expected = [(as_of+timedelta(days=i)).strftime('%Y%m%d') for i in range(5)]
    by_date = {}
    for f in all_rows:
        require(str(f['areaCode']) == '410100', 'forecast city differs')
        require(f['dataDate'] not in by_date, 'duplicate forecast day')
        by_date[f['dataDate']] = f
    require(all(d in by_date for d in expected), 'forecast window incomplete or stale')
    rows = [by_date[d] for d in expected]
    require(all(0 <= f['aqi_min'] <= f['aqi_max'] <= 500 for f in rows), 'invalid AQI forecast')
    tree = html.fromstring((root/'weather.html').read_text())
    weather = []
    for i,li in enumerate(tree.xpath('//ul[contains(@class,"t clearfix")]/li')[:5]):
        day = li.xpath('./h1')[0].text_content()
        require(int(re.search(r'\d+',day)[0]) == (as_of+timedelta(days=i)).day, 'weather date differs')
        weather.append({'description':li.xpath('./p[@class="wea"]')[0].text_content(),
            'high':li.xpath('./p[@class="tem"]/span')[0].text,
            'low':li.xpath('./p[@class="tem"]/i')[0].text.replace('℃',''),
            'wind':'转'.join(dict.fromkeys(li.xpath('./p[@class="win"]/em/span/@title'))),
            'force':li.xpath('./p[@class="win"]/i')[0].text.replace('<','小于')})
    require(len(weather)==5, 'weather forecast incomplete')
    return rows, weather


def _chart_font(runtime):
    if runtime and runtime.get('font_family'):
        family = runtime['font_family']
        font_manager.findfont(font_manager.FontProperties(family=family), fallback_to_default=False)
        return family
    for family in ('Songti SC', 'SimSun', 'Noto Serif CJK SC', 'PingFang SC', 'Noto Sans CJK SC'):
        try:
            font_manager.findfont(font_manager.FontProperties(family=family), fallback_to_default=False)
            return family
        except (ValueError, RuntimeError):
            continue
    return 'DejaVu Sans'


def make_charts(period, output, runtime=None):
    chart_font = _chart_font(runtime)
    output.mkdir(exist_ok=True)
    current=period['current'];rows=current['rows'];peak=current['peak']
    plt.rcParams.update({'font.family':[chart_font, 'DejaVu Sans'],'axes.unicode_minus':False,'font.size':10})
    def save(fig,name):
        fig.savefig(output/name,dpi=190,bbox_inches='tight',facecolor=fig.get_facecolor())
        plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(8,3.1))
    for ax,values,title in zip(axes,[current['iaqi_totals'],peak['iaqi']],
                               ['本期累计分指数构成',date_text(peak['date'])+'  AQI '+str(peak['aqi'])]):
        if sum(values):
            wedges,_,_=ax.pie(values,colors=COLORS,startangle=90,autopct='%1.1f%%',pctdistance=.76,textprops={'fontsize':9})
        else:
            ax.text(.5,.5,'本期分指数均为零',ha='center');ax.set_axis_off()
            wedges=[plt.Rectangle((0,0),1,1,color=c) for c in COLORS]
        ax.set_title(title,fontsize=11)
    fig.legend(wedges,LABELS,loc='lower center',ncol=6,frameon=False)
    fig.subplots_adjust(bottom=.15);save(fig,'pies.png')
    start,end=date.fromisoformat(period['start']),date.fromisoformat(period['end'])
    dates=[(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    by_date={r['date']:r for r in rows};xs=list(range(len(dates)))
    step=max(1,math.ceil(len(dates)/8))
    ticks=list(range(0,len(dates),step))
    if ticks[-1] != len(dates)-1:
        if len(ticks)>1 and len(dates)-1-ticks[-1]<step:
            ticks.pop()
        ticks.append(len(dates)-1)
    labels=[date_text(dates[i]) for i in ticks]
    fig,ax=plt.subplots(figsize=(8,2.4))
    ys=[by_date[d]['aqi'] if d in by_date else math.nan for d in dates]
    ax.plot(xs,ys,color='#4472C4',marker='o',markersize=4)
    for x,y in zip(xs,ys):
        if math.isfinite(y):ax.annotate(str(int(y)),(x,y),xytext=(0,7),textcoords='offset points',ha='center',fontsize=8)
    ax.axhline(100,color='#ED7D31',ls='--',lw=1)
    ax.set_xticks(ticks,labels,fontsize=8);ax.set_ylim(0,max(160,current['aqi_max']+25));ax.set_ylabel('AQI');ax.grid(axis='y',alpha=.2)
    save(fig,'aqi.png')
    dark='#17212F';fig,axes=plt.subplots(3,2,figsize=(8,8.2));fig.patch.set_facecolor(dark)
    for i,ax in enumerate(axes.flat):
        ax.set_facecolor(dark)
        ax.plot(xs,[by_date[d]['values'][i] if d in by_date else math.nan for d in dates],color=COLORS[i],marker='o',markersize=3,lw=1.6)
        ax.set_title(LABELS[i]+(' 日最大8小时' if i==3 else ' 日均'),color='white',fontsize=11)
        ax.set_ylabel('mg/m³' if i==2 else 'μg/m³',color='#CBD5E1',fontsize=9)
        ax.set_xticks(ticks,labels,fontsize=7);ax.tick_params(colors='#CBD5E1');ax.grid(alpha=.15)
        for spine in ax.spines.values():spine.set_color('#607080')
    fig.tight_layout(pad=2);save(fig,'trends.png')


def render(period, advice, source_root, output, region='郑州市', runtime=None):
    if runtime is None:
        from .render import preflight
        runtime = preflight()
    require(advice.get('kind') == period['kind'], 'advice period differs')
    require(advice.get('period_facts_sha256') == period['facts_sha256'], 'stale advice fact binding')
    require(advice.get('facts') == advice_facts(period), 'advice execution horizon or facts changed')
    require(advice.get('validation') == 'rules_and_independent_model_review_passed', 'unreviewed advice')
    require(advice.get('gpu',{}).get('size_vram',0)>0, 'GPU usage unverified')
    advice_paragraphs=validate_advice({'paragraphs':advice['paragraphs']},period['kind'])
    forecast,weather=forecasts(source_root,date.fromisoformat(period['as_of']))
    output.parent.mkdir(parents=True,exist_ok=True);assets=output.parent/'assets';make_charts(period,assets,runtime)
    doc=new_document(region, font_name=runtime.get('docx_font_family'));s=period['current'];name=period['name'];kind=period['kind']
    for _ in range(5):paragraph(doc,'',space=12)
    paragraph(doc,'空气质量研判'+name,36,align=WD_ALIGN_PARAGRAPH.CENTER,space=18)
    paragraph(doc,date_range(period['start'],period['end']),14,align=WD_ALIGN_PARAGRAPH.CENTER,space=12)
    paragraph(doc,'编制日期：'+date_text(period['as_of']),10.5,align=WD_ALIGN_PARAGRAPH.CENTER)
    page_break(doc)
    heading(doc,'空气质量情况')
    summary=(f"{date_range(period['start'],period['end'])}，{region}纳入统计{s['days']}天，"
             f"优良{s['good_days']}天，占有效天数的{s['good_percent']:.1f}%；"
             f"轻度污染{s['counts'][2]}天，AQI范围为{s['aqi_min']}—{s['aqi_max']}。")
    if s['missing']:
        summary+='暂无有效日数据的日期为'+ '、'.join(date_text(x) for x in s['missing'])+'，以下统计按有效日计算。'
    paragraph(doc,summary)
    heading(doc,'（一）空气质量情况表',True)
    rows=[['本期日值平均']+[f'{v:.2f}' if i==2 else f'{v:.1f}' for i,v in enumerate(s['means'])],
          ['期内日值最高']+[f'{max(r["values"][i] for r in s["rows"]):g}' for i in range(6)],
          ['期内日值最低']+[f'{min(r["values"][i] for r in s["rows"]):g}' for i in range(6)]]
    table(doc,['统计项目']+list(LABELS),rows,[2.6]+[2.0]*6,9)
    paragraph(doc,'单位：CO为mg/m³，其余为μg/m³。臭氧按每日最大8小时浓度统计，其余按日均浓度统计。',8.5)
    if period['comparable']:
        prev=period['previous'];change=s['means'][5]-prev['means'][5]
        paragraph(doc,f"与{date_text(period['previous_start'])}至{date_text(period['previous_end'])}相比，细颗粒物日值平均{'增加' if change>=0 else '减少'}{abs(change):.1f} μg/m³。",9)
    quality=lambda a:'优' if a<=50 else '良' if a<=100 else '轻度污染' if a<=150 else '中度污染' if a<=200 else '重度污染' if a<=300 else '严重污染'
    entries=[[date_text(r['date']),r['aqi'],quality(r['aqi']),f'{r["values"][3]:g}',f'{r["values"][4]:g}',f'{r["values"][5]:g}'] for r in s['rows']]
    for offset in range(0,len(entries),15):
        if offset:page_break(doc)
        caption=paragraph(doc,date_range(period['start'],period['end'])+'逐日空气质量情况'+('（续）' if offset else ''),9,align=WD_ALIGN_PARAGRAPH.CENTER)
        caption.paragraph_format.keep_with_next=True
        table(doc,['日期','AQI','空气质量','O₃','PM₁₀','PM₂.₅'],entries[offset:offset+15],[2.5,1.6,3.0,2.5,2.5,2.5],8.5)
    page_break(doc)
    heading(doc,'（二）六项污染物分指数占比分析',True)
    peak=s['peak'];ozone=s['shares'][3]
    paragraph(doc,f"本期臭氧累计分指数占比为{ozone:.1f}%。{date_text(peak['date'])}AQI达到期内最高值{peak['aqi']}。")
    image(doc,assets/'pies.png')
    table(doc,['统计范围']+list(LABELS),[['本期累计']+s['iaqi_totals'],[date_text(peak['date'])]+peak['iaqi']],[2.6]+[2.0]*6,9)
    paragraph(doc,'本期占比按各污染物逐日分指数累计值占六项累计值之和计算；最高日按当日分指数计算。',8.5)
    heading(doc,'近期空气质量变化',True);image(doc,assets/'aqi.png')
    paragraph(doc,f"本期最低AQI为{s['aqi_min']}，最高为{s['aqi_max']}。最后一个有效日为{date_text(s['rows'][-1]['date'])}，AQI为{s['rows'][-1]['aqi']}，空气质量为{quality(s['rows'][-1]['aqi'])}。")
    page_break(doc);heading(doc,'采集指标变化趋势分析')
    paragraph(doc,date_range(period['start'],period['end'])+'六项污染物浓度变化情况如下。CO单位为mg/m³，其余为μg/m³。')
    image(doc,assets/'trends.png')
    paragraph(doc,f"本期颗粒物日值平均：PM₁₀为{s['means'][4]:.1f} μg/m³，PM₂.₅为{s['means'][5]:.1f} μg/m³。臭氧日最大8小时浓度介于{min(r['values'][3] for r in s['rows']):g}—{max(r['values'][3] for r in s['rows']):g} μg/m³。")
    page_break(doc);heading(doc,'环境空气质量预测');heading(doc,'（一）空气质量形势预报',True)
    for f,w in zip(forecast[:3],weather[:3]):
        label=date_text(f['dataDate'][:4]+'-'+f['dataDate'][4:6]+'-'+f['dataDate'][6:])
        level=quality(f['aqi_min']);upper=quality(f['aqi_max']);level=level if level==upper else level+'至'+upper
        pollutants=f['primaryPol'].replace('O3','臭氧').replace('PM2.5','细颗粒物').replace(',','、')
        paragraph(doc,f"{label}，{w['description']}，气温{w['low']}—{w['high']}℃，{w['wind']}，风力{w['force']}。预测全天空气质量为{level}，AQI：{f['aqi_min']}—{f['aqi_max']}，首要污染物为{pollutants}。")
    heading(doc,'（二）未来五天AQI数值预报',True)
    table(doc,[date_text(f['dataDate'][:4]+'-'+f['dataDate'][4:6]+'-'+f['dataDate'][6:]) for f in forecast],
          [[f"{f['aqi_min']}—{f['aqi_max']}" for f in forecast],
           [quality(f['aqi_min'])+('—'+quality(f['aqi_max']) if quality(f['aqi_min'])!=quality(f['aqi_max']) else '') for f in forecast],
           [f['primaryPol'].replace('O3','O₃').replace('PM2.5','PM₂.₅').replace(',','／') for f in forecast],
           [w['description'] for w in weather]],[2.92]*5,9)
    page_break(doc);heading(doc,'工作建议')
    for text in advice_paragraphs:
        p=paragraph(doc,text,10.5,space=13);p.paragraph_format.first_line_indent=Pt(21)
    visible='\n'.join(t.text for t in doc._element.body.iter(qn('w:t')))
    require(not re.search(r'https?://|\[\d+\]|公开|数据来源|来源索引|外部网站|GPU|qwen|DataV',visible), 'source annotation leaked into report')
    doc.save(output)
    return {'style_version':TEMPLATE_VERSION + '+' + RENDERER_VERSION,'period':period,'advice':advice,
            'template_sha256':TEMPLATE_SHA256,
            'text_sha256':hashlib.sha256(visible.encode()).hexdigest(),
            'runtime':{k:v for k,v in runtime.items() if k!='env'},
            'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source_root.iterdir() if p.is_file()}}


def convert_pdf(docx, soffice):
    with tempfile.TemporaryDirectory(prefix='airmore-product-lo-') as tmp:
        env=os.environ.copy()
        config=Path('/opt/homebrew/etc/fonts/fonts.conf')
        if config.exists():env.setdefault('FONTCONFIG_FILE',str(config))
        cmd=[str(soffice),'-env:UserInstallation='+Path(tmp).as_uri(),'--headless','--convert-to','pdf','--outdir',str(docx.parent),str(docx)]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=120,env=env)
        require(result.returncode==0 and docx.with_suffix('.pdf').is_file(),'PDF conversion failed')
    return docx.with_suffix('.pdf')
