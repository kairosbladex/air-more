"""User-approved original-template report with physical-hangar statistics.

This renderer only consumes calculated index, scoped observations, verified
forecast snapshots and validated advice. It never queries a city measurement
feed or fills values from the sample document.
"""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import math
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as E
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle, Ellipse
from docx import Document

from . import strict_template as st
from .models import METRICS, survey_value_text
from .report_index import evaluate, VERSION as INDEX_VERSION
from .report_comparison import build_comparison, ranked_hangar_ids

VERSION='approved-hangar-report/2.3'
TEMPLATE_SHA256='e69f62460592f22812baf103dbf5ca2381c24fe6f5563814787d9a4559b99c44'


def level(v):
 for cap,label in [(50,'优'),(100,'良'),(150,'轻度污染'),(200,'中度污染'),(300,'重度污染'),(500,'严重污染')]:
  if v<=cap:return label
 raise ValueError(v)


def temperature_text(low, high):
 low=None if low is None else str(low).strip().removesuffix('℃').strip()
 high=None if high is None else str(high).strip().removesuffix('℃').strip()
 if low is None and high is None:return '暂无'
 if high is None:return f'最低{low}℃'
 if low is None:return f'最高{high}℃'
 return f'{low}—{high}℃'

def pie(record, runtime):
 order=tuple(m for m in ('so2','no2','pm10','pm2_5','o3','co') if record and m in (record.get('ratios') or {}))
 if not record or not record.get('ratios'):
  with plt.rc_context({'font.family':[runtime['font_family'],'DejaVu Sans']}):
   fig,ax=plt.subplots(figsize=(3.5,2.9));ax.pie([1],colors=['#eeeeee'])
   ax.text(0,0,'该期无可计算分指数',ha='center',va='center',fontsize=10)
   ax.set_xlim(-1.95,1.95)
   fig.subplots_adjust(left=.01,right=.99,bottom=.22,top=.99)
   return st.png(fig)
 assert set(record['ratios']) <= set(METRICS) and abs(sum(record['ratios'].values())-1)<1e-10
 with plt.rc_context({'font.family':[runtime['font_family'],'DejaVu Sans']}):
  fig,ax=plt.subplots(figsize=(3.5,2.9))
  wedges,*_=ax.pie([record['ratios'][m] for m in order],colors=[st.COLORS[m] for m in order],startangle=90,autopct=lambda v:f'{v:.1f}%' if v>=4 else '',pctdistance=.7,textprops={'fontsize':9},wedgeprops={'edgecolor':'white','linewidth':.45})
  def percent(m):
   value=record['ratios'][m]*100
   return ('<0.0001' if 0<value<.0001 else f'{value:.4f}'.rstrip('0').rstrip('.'))+'%'
  # Keep true angles, while making every small component readable outside
  # the circle. Spread labels on each side to prevent adjacent tiny slices
  # from hiding each other; the legend retains each valid exact percentage.
  small={-1:[],1:[]}
  for m,wedge in zip(order,wedges):
   if 0<record['ratios'][m]<.04:
    angle=math.radians((wedge.theta1+wedge.theta2)/2)
    x,y=math.cos(angle),math.sin(angle)
    small[-1 if x<0 else 1].append((y,x,m))
  for side,items in small.items():
   items.sort()
   for j,(y,x,m) in enumerate(items):
    label_y=y if len(items)==1 else -.65+1.5*j/(len(items)-1)
    elbow=side*(1.1+.08*j)
    ax.plot([x,x*1.1,elbow,elbow,side*1.35],
            [y,y*1.1,y*1.1,label_y,label_y],color=st.COLORS[m],lw=.75)
    ax.text(side*1.4,label_y,st.LABELS[m]+'\n'+percent(m),
            ha='left' if side>0 else 'right',va='center',fontsize=8)
  if any(small.values()):ax.set_xlim(-1.95,1.95)
  labels=[st.LABELS[m]+' '+percent(m) for m in order]
  ax.legend(wedges,labels,loc='lower center',bbox_to_anchor=(.5,-.26),ncol=3,frameon=False,fontsize=7.5,handlelength=.7,columnspacing=.9,handletextpad=.35)
  fig.subplots_adjust(left=.01,right=.99,bottom=.22,top=.99)
  return st.png(fig)

def forecast_card(context, runtime):
 frows=context.get('forecasts',[]);weather=context.get('weather',[])
 if not frows or len(weather)!=5:
  # The five-day window starts at the report's issue date, including when
  # that historical forecast is missing.
  return st.forecast((date.fromisoformat(context['issue_date'])-timedelta(days=1)).isoformat(),runtime)
 with plt.rc_context({'font.family':[runtime['font_family'],'DejaVu Sans']}):
  fig,ax=plt.subplots(figsize=(9,4.2));ax.axis('off');ax.set_xlim(0,1);ax.set_ylim(0,1)
  for j,(f,w) in enumerate(zip(frows,weather)):
   x=(j+.5)/5
   if j==0:ax.axvspan(0,.2,color='#f4f4f4')
   if j:ax.plot([j/5,j/5],[.04,.98],color='#dedede',lw=.6)
   lo,hi=level(f['aqi_min']),level(f['aqi_max']); quality=lo if lo==hi else lo+'至'+hi
   day=date.fromisoformat(f['dataDate'][:10])
   ax.text(x,.925,f"{day.month}月{day.day}日",ha='center',fontsize=15)
   ax.text(x,.79,f"{f['aqi_min']}—{f['aqi_max']}",ha='center',fontsize=17)
   c='#e8a34b' if f['aqi_max']>100 else '#b4bb27'
   ax.add_patch(FancyBboxPatch((x-.089,.64),.178,.085,boxstyle='round,pad=0.004,rounding_size=.02',facecolor=c,edgecolor='none'))
   ax.text(x,.682,quality,ha='center',va='center',color='white',fontsize=11)
   ax.text(x,.56,f['primaryPol'].replace(',',' / ') or '首要污染物未发布',ha='center',fontsize=9 if not f['primaryPol'] else 10.5)
   # Simple weather symbols retain the source's five-column forecast card layout.
   if '多云' in w['description']:
    ax.add_patch(Circle((x-.025,.445),.027,color='#f3c160'))
   for dx,dy,rx,ry in [(-.016,0,.05,.035),(.015,.01,.055,.046),(0,-.007,.082,.032)]:
    ax.add_patch(Ellipse((x+dx,.43+dy),rx,ry,facecolor='#aebdcc',edgecolor='none'))
   if '雨' in w['description']:
    for dx in (-.024,0,.024):ax.plot([x+dx,x+dx-.007],[.397,.378],color='#5192c8',lw=1.6)
   ax.text(x,.32,w['description'],ha='center',fontsize=12)
   ax.text(x,.226,temperature_text(w['low'],w['high']),ha='center',fontsize=12)
   ax.text(x,.139,w['wind'],ha='center',fontsize=10)
   ax.text(x,.063,w['force'],ha='center',fontsize=11)
  fig.subplots_adjust(left=.005,right=.995,bottom=.01,top=.99)
  return st.png(fig)

def measured_trend(h,runtime):
 axis=h.get('platform_trend')
 if not axis:return st.trend(h,runtime)
 # The platform overlays native concentration series in a single chart.
 # Match the platform's zero display at missing/invalid timeline positions.
 # Use a new plotting list: source nulls must stay excluded from calculations.
 palette=('#5470c6','#91cc75','#fac858','#ee6666','#73c0de','#3ba272',
          '#fc8452','#9a60b4','#ea7ccc')
 colors=dict(zip(('pm10','pm2_5','pm1_0','co','so2','no2','o3','ox','ch4'),palette))
 with plt.rc_context({'font.family':[runtime['font_family'],'DejaVu Sans'],'axes.unicode_minus':False}):
  legend_rows=max(1,math.ceil(len(axis['series'])/6))
  fig,ax=plt.subplots(figsize=(8.4,3.8+.32*legend_rows),facecolor='#031426')
  ax.set_facecolor('#031426')
  labels=[label.replace('~','\n') for label in axis['labels']]
  for i,item in enumerate(axis['series']):
   color=colors.get(item['metric'],palette[i%len(palette)])
   values=[v if v is not None else 0 for v in item['values']]
   ax.plot(range(len(labels)),values,
           label=item['name'].translate(str.maketrans('₀₁₂₃₄₅','012345')),
           color=color,linewidth=1.5,marker='o',markersize=2.5)
   ax.fill_between(range(len(labels)),values,0,color=color,alpha=.16)
  step=max(1,math.ceil(len(labels)/5))
  ticks=list(range(0,len(labels),step))
  ax.set_xticks(ticks,[labels[i] for i in ticks])
  ax.tick_params(colors='white',labelsize=11)
  ax.grid(alpha=.4,axis='y',linestyle=':',color='#9bbfda')
  measured=[v for item in axis['series'] for v in item['values'] if v is not None]
  if not measured or min(measured)>=0:ax.set_ylim(bottom=0)
  for name,spine in ax.spines.items():
   spine.set_color('#63829b');spine.set_visible(name in ('left','bottom'))
  if axis['series']:
   # Matplotlib fills legend columns vertically; reorder only the handles so
   # the visible rows retain the platform's left-to-right indicator order.
   handles,names=ax.get_legend_handles_labels()
   columns=min(6,len(handles))
   order=[i for col in range(columns) for i in range(col,len(handles),columns)]
   ax.legend([handles[i] for i in order],[names[i] for i in order],
             loc='lower center',bbox_to_anchor=(.5,1.05),ncol=columns,frameon=False,
             labelcolor='white',fontsize=11,handlelength=1.2,columnspacing=1.3)
  if not measured:
   ax.set_ylim(0,1)
   ax.text(.5,.45,'无有效数据',ha='center',color='white',transform=ax.transAxes,fontsize=12)
  fig.subplots_adjust(left=.09,right=.98,top=1-(.25+.32*legend_rows)/(3.8+.32*legend_rows),bottom=.19)
  return st.png(fig)


def pprop(p, name, value):
    pp=p.find('w:pPr',st.NS)
    if pp is None:pp=E.SubElement(p,'{'+st.NS['w']+'}pPr')
    node=pp.find('w:'+name,st.NS)
    if node is None:node=E.SubElement(pp,'{'+st.NS['w']+'}'+name)
    node.set('{'+st.NS['w']+'}val',value)
    return pp


def center_report_table(table):
    """Center a flowing report table within the document text area."""
    props=table.find('w:tblPr',st.NS)
    if props is None:props=E.Element('{'+st.NS['w']+'}tblPr');table.insert(0,props)
    for tag in ('jc','tblInd'):
        for old in props.findall('w:'+tag,st.NS):props.remove(old)
    width=props.find('w:tblW',st.NS)
    at=list(props).index(width)+1 if width is not None else 0
    props.insert(at,E.Element('{'+st.NS['w']+'}jc',{'{'+st.NS['w']+'}val':'center'}))
    props.insert(at+1,E.Element('{'+st.NS['w']+'}tblInd',
                                {'{'+st.NS['w']+'}w':'0','{'+st.NS['w']+'}type':'dxa'}))


def range_text(source):
    a,b=date.fromisoformat(source['start']),date.fromisoformat(source['end'])
    if a==b:return f'{a.year}年{a.month}月{a.day}日'
    return f'{a:%Y-%m-%d}至{b:%Y-%m-%d}'


def compact_paragraph(template, content, *, center=False, size=18):
    p=deepcopy(template)
    for child in list(p):
        if child.tag!='{'+st.NS['w']+'}pPr':p.remove(child)
    st.text(p,content)
    pp=pprop(p,'jc','center' if center else 'left')
    for tag in ('ind','tabs','numPr','spacing'):
        for node in pp.findall('w:'+tag,st.NS):pp.remove(node)
    E.SubElement(pp,'{'+st.NS['w']+'}spacing',{'{'+st.NS['w']+'}before':'0','{'+st.NS['w']+'}after':'0','{'+st.NS['w']+'}line':'240','{'+st.NS['w']+'}lineRule':'auto'})
    for run in p.findall('w:r',st.NS):
        prop=run.find('w:rPr',st.NS)
        if prop is None:
            base=pp.find('w:rPr',st.NS)
            prop=deepcopy(base) if base is not None else E.Element('{'+st.NS['w']+'}rPr')
            run.insert(0,prop)
        for tag in ('sz','szCs'):
            node=prop.find('w:'+tag,st.NS)
            if node is None:node=E.SubElement(prop,'{'+st.NS['w']+'}'+tag)
            node.set('{'+st.NS['w']+'}val',str(size))
    return p


def align_pie_pair(row, labels, width):
    """Give each title and its picture the same centered half-width cell."""
    cell=row.find('w:tc',st.NS)
    drawings=[deepcopy(d) for d in cell.xpath('.//w:drawing',namespaces=st.NS)]
    assert len(drawings)==2
    template=cell.find('w:p',st.NS)
    for child in list(cell):
        if child.tag!='{'+st.NS['w']+'}tcPr':cell.remove(child)
    outer_props=cell.find('w:tcPr',st.NS)
    outer_margins=E.SubElement(outer_props,'{'+st.NS['w']+'}tcMar')
    for edge in ('top','left','bottom','right'):
        E.SubElement(outer_margins,'{'+st.NS['w']+'}'+edge,{'{'+st.NS['w']+'}w':'0','{'+st.NS['w']+'}type':'dxa'})
    table=E.SubElement(cell,'{'+st.NS['w']+'}tbl')
    props=E.SubElement(table,'{'+st.NS['w']+'}tblPr')
    E.SubElement(props,'{'+st.NS['w']+'}tblW',{'{'+st.NS['w']+'}w':str(width),'{'+st.NS['w']+'}type':'dxa'})
    E.SubElement(props,'{'+st.NS['w']+'}tblLayout',{'{'+st.NS['w']+'}type':'fixed'})
    center_report_table(table)
    borders=E.SubElement(props,'{'+st.NS['w']+'}tblBorders')
    for edge in ('top','left','bottom','right','insideH','insideV'):
        E.SubElement(borders,'{'+st.NS['w']+'}'+edge,{'{'+st.NS['w']+'}val':'nil'})
    margins=E.SubElement(props,'{'+st.NS['w']+'}tblCellMar')
    for edge in ('top','left','bottom','right'):
        E.SubElement(margins,'{'+st.NS['w']+'}'+edge,{'{'+st.NS['w']+'}w':'0','{'+st.NS['w']+'}type':'dxa'})
    grid=E.SubElement(table,'{'+st.NS['w']+'}tblGrid')
    widths=[width//2,width-width//2]
    for value in widths:E.SubElement(grid,'{'+st.NS['w']+'}gridCol',{'{'+st.NS['w']+'}w':str(value)})
    inner=E.SubElement(table,'{'+st.NS['w']+'}tr')
    for label,drawing,value in zip(labels,drawings,widths):
        side=E.SubElement(inner,'{'+st.NS['w']+'}tc');cp=E.SubElement(side,'{'+st.NS['w']+'}tcPr')
        E.SubElement(cp,'{'+st.NS['w']+'}tcW',{'{'+st.NS['w']+'}w':str(value),'{'+st.NS['w']+'}type':'dxa'})
        cell_borders=E.SubElement(cp,'{'+st.NS['w']+'}tcBorders')
        for edge in ('top','left','bottom','right'):
            E.SubElement(cell_borders,'{'+st.NS['w']+'}'+edge,{'{'+st.NS['w']+'}val':'nil'})
        caption=compact_paragraph(template,label,center=True);pprop(caption,'keepNext','1');side.append(caption)
        picture=compact_paragraph(template,'',center=True);pprop(picture,'keepNext','0')
        picture.find('w:r',st.NS).append(drawing);side.append(picture)
    tail=compact_paragraph(template,'',size=2);pprop(tail,'keepNext','0');cell.append(tail)


def comparison_table(reference, rows):
    table=deepcopy(reference)
    patterns=table.findall('w:tr',st.NS)[:2]
    for row in table.findall('w:tr',st.NS):table.remove(row)
    grid=table.find('w:tblGrid',st.NS)
    total=sum(int(c.get('{'+st.NS['w']+'}w')) for c in grid)
    widths=[round(total*w) for w in (.23,.2,.17,.17,.23)];widths[-1]+=total-sum(widths)
    for c in list(grid):grid.remove(c)
    for width in widths:E.SubElement(grid,'{'+st.NS['w']+'}gridCol',{'{'+st.NS['w']+'}w':str(width)})
    values=[['机库名称','指标','比较期','本期','变化']]+[
        [r['hangar_name'],r['label']+(' ('+r['unit']+')' if r['unit'] else ''),r['previous_text'],r['current_text'],r['change_text']] for r in rows]
    previous=None
    for i,contents in enumerate(values):
        row=deepcopy(patterns[0 if i==0 else 1]);cells=row.findall('w:tc',st.NS)
        for c in cells:row.remove(c)
        for j,(content,width) in enumerate(zip(contents,widths)):
            cell=deepcopy(cells[0 if j==0 else 1]);cp=cell.find('w:tcPr',st.NS)
            for tag in ('gridSpan','vMerge'):
                for node in cp.findall('w:'+tag,st.NS):cp.remove(node)
            cp.find('w:tcW',st.NS).set('{'+st.NS['w']+'}w',str(width))
            if i and j==0:
                hid=rows[i-1]['hangar_id'];same=hid==previous
                E.SubElement(cp,'{'+st.NS['w']+'}vMerge',{'{'+st.NS['w']+'}val':'continue' if same else 'restart'})
                if same:content=''
                previous=hid
            template=cell.find('w:p',st.NS)
            for node in list(cell):
                if node is not cp:cell.remove(node)
            paragraph=compact_paragraph(template,content,center=True);pprop(paragraph,'keepNext','1' if i==0 else '0')
            cell.append(paragraph);row.append(cell)
        table.append(row)
    return table


def fill_rank_table(table, headers, values, metric_count, is_mean):
    """Rebuild only this table's columns, retaining the reference cell typography."""
    rows=table.findall('w:tr',st.NS)
    templates=[[deepcopy(c) for c in row.findall('w:tc',st.NS)] for row in rows[:2]]
    original_grid=table.find('w:tblGrid',st.NS)
    total=sum(int(c.get('{'+st.NS['w']+'}w')) for c in original_grid)
    weights=[2.15]+[.85]*metric_count+[1.15,.5]+([1.1] if is_mean else [])
    widths=[round(total*w/sum(weights)) for w in weights];widths[-1]+=total-sum(widths)
    for c in list(original_grid):original_grid.remove(c)
    for width in widths:E.SubElement(original_grid,'{'+st.NS['w']+'}gridCol',{'{'+st.NS['w']+'}w':str(width)})
    patterns=[deepcopy(rows[0]),deepcopy(rows[1])]
    for row in rows:table.remove(row)
    source_columns=[0]+[1]*metric_count+[7,8]+([8] if is_mean else [])
    for i,contents in enumerate([headers]+values):
        row=deepcopy(patterns[0 if i==0 else 1])
        for cell in row.findall('w:tc',st.NS):row.remove(cell)
        for j,value in enumerate(contents):
            cell=deepcopy(templates[0 if i==0 else 1][source_columns[j]])
            st.text(cell,value)
            props=cell.find('w:tcPr',st.NS)
            for span in props.findall('w:gridSpan',st.NS):props.remove(span)
            width=props.find('w:tcW',st.NS)
            width.set('{'+st.NS['w']+'}w',str(widths[j]));width.set('{'+st.NS['w']+'}type','dxa')
            for prop in cell.xpath('.//w:rPr',namespaces=st.NS):
                for tag in ('sz','szCs'):
                    node=prop.find('w:'+tag,st.NS)
                    if node is None:node=E.SubElement(prop,'{'+st.NS['w']+'}'+tag)
                    node.set('{'+st.NS['w']+'}val','18')
            row.append(cell)
        for height in row.findall('w:trPr/w:trHeight',st.NS):height.set('{'+st.NS['w']+'}hRule','atLeast')
        table.append(row)


def render_document(stats, analyses, composite, cfg, output, runtime):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    assert hashlib.sha256(st.TEMPLATE.read_bytes()).hexdigest()==TEMPLATE_SHA256
    if composite.get('version') != INDEX_VERSION:
        composite=evaluate(stats)
    # Keep caller statistics intact while all report panels use the period rank.
    order=ranked_hangar_ids(stats,composite,'last_day' if stats['request']['report_type']=='DAY' else 'current')
    stats=dict(stats,hangars={hid:stats['hangars'][hid] for hid in order})
    comparison=build_comparison(stats,composite)
    context=stats.get('forecast') or {'issue_date':stats['request']['end_date'],'forecasts':[],'weather':[]}
    result=st.render_document(stats,analyses,composite,cfg,output,runtime,
                             pie_renderer=pie,trend_renderer=measured_trend,
                             forecast_renderer=lambda end,rt:forecast_card(context,rt))
    target=output/'report.docx'
    with ZipFile(target) as z:parts={name:z.read(name) for name in z.namelist()}
    root=st.xml(parts['word/document.xml']);body=root.find('w:body',st.NS);slots=list(body)
    if stats['data_kind'] != 'synthetic':
        st.text(slots[0], '')
    st.text(slots[6],'空气质量研判报告')
    # Replace the reference cover's trailing spacer paragraphs with a stable
    # page boundary; otherwise short DAY captions leave half an empty page.
    # Keep the first paragraph after the floating cover table as its anchor.
    for spacer in slots[13:20]:
        body.remove(spacer)
    pprop(slots[20],'pageBreakBefore','1')
    st.text(slots[21],'（一）各机库空气质量情况表')
    expected=[]
    metrics=stats.get('indicator_catalog',{}).get('table_metrics',list(METRICS))
    kind=stats['request']['report_type']
    unit_sets={m:{h['current']['survey'][m]['unit'] for h in stats['hangars'].values()
                  if h['current']['survey'][m]['unit']} for m in metrics}
    for idx,period,caption in ((23,'last_day',22),(26,'current',25)):
        if kind=='DAY' and period=='current':
            body.remove(slots[caption]);body.remove(slots[idx])
            continue
        exemplar=next(iter(stats['hangars'].values()))['current']
        label=range_text(dict(start=exemplar['end'],end=exemplar['end'])) if period=='last_day' else range_text(exemplar)
        st.text(slots[caption],label+'各机库空气质量排名表')
        headers=['机库名称']+[st.LABELS[m] for m in metrics]+['综合指数','名次']
        values=[]
        ordered=ranked_hangar_ids(stats,composite,period)
        for hid in ordered:
            h=stats['hangars'][hid]
            survey=(h['days'][h['current']['end']] if period=='last_day' else h['current'])['survey']
            record=composite['hangars'][hid][period]
            row=[h['name']]
            for m in metrics:
                cell=survey[m];value=cell['value'] if cell['unit'] else None
                value_text=survey_value_text(value,cell['unit'],cell.get('precision')) if value is not None else '/'
                if value is not None and len(unit_sets[m])>1:value_text+=' '+cell['unit']
                row.append(value_text)
            value=record['rank_value']
            row+= [str(value) if value is not None else '/',
                   str(record['rank']) if record['rank'] is not None else '/']
            values.append(row)
        fill_rank_table(slots[idx],headers,values,len(metrics),False)
        expected.append(values)
    body.remove(slots[27])
    st.text(slots[28],'（二）各机库污染物分指数占比分析')
    pie_width=sum(int(c.get('{'+st.NS['w']+'}w')) for c in slots[29].find('w:tblGrid',st.NS))
    for i,(hid,h) in enumerate(stats['hangars'].items()):
        rows=slots[29].findall('w:tr',st.NS)
        st.text(rows[2*i].findall('w:tc',st.NS)[0],'机库名称')
        prev,cur=h['previous'],h['current']
        if kind=='DAY':
            a,b=date.fromisoformat(prev['end']),date.fromisoformat(cur['end'])
            labels=(f'{a.month}月{a.day}日占比',f'{b.month}月{b.day}日占比')
        else:labels=('比较期占比','本期占比')
        align_pie_pair(rows[2*i+1],labels,pie_width)
    st.text(slots[30],'各机库指标变化趋势分析')
    st.text(slots[31].find('w:tr/w:tc',st.NS),'机库名称')
    for i,(hid,h) in enumerate(stats['hangars'].items()):
        cell=slots[31].findall('w:tr',st.NS)[i+1].findall('w:tc',st.NS)[1]
        paragraph=cell.findall('w:p',st.NS)[-1]
        if not paragraph.xpath('.//w:drawing',namespaces=st.NS):cell.remove(paragraph)
    fs=context.get('forecasts',[]);weather=context.get('weather',[])
    st.text(slots[34], '（一）空气质量形势预报（发布：'+context['issue_date']+'）')
    if len(fs)==len(weather)==5:
        for i,(f,w) in enumerate(zip(fs[:3],weather[:3])):
            low,high=level(f['aqi_min']),level(f['aqi_max']);quality=low if low==high else low+'至'+high
            pollutants=f['primaryPol'].replace('O3','臭氧').replace('PM2.5','细颗粒物').replace(',','、')
            primary='首要污染物为'+pollutants+'。' if pollutants else '首要污染物未发布。'
            provider='（'+f['provider']+'）' if f.get('provider') else ''
            day=date.fromisoformat(w['date'])
            st.text(slots[35+i],f"{day.month}月{day.day}日，{w['description']}，气温{temperature_text(w['low'],w['high'])}，{w['wind']}，风力{w['force']}。{context['city_name']}AQI预报{provider}为{f['aqi_min']}—{f['aqi_max']}，空气质量{quality}，{primary}")
    else:
        st.text(slots[35],'该报告日期的预报资料暂不可用，未填入其他日期的预报。')
        st.text(slots[36],'');st.text(slots[37],'')
    for idx in (28,30,33,42):pprop(slots[idx],'pageBreakBefore','1')
    # Rebuild only the recommendation paragraphs from validated advice. Do not
    # append model facts, provenance footers, disclaimers or chart methodology.
    advice=[]
    for a in analyses.values():
        audit=a.metadata.get('report_advice',{})
        if audit.get('recommendations'):
            advice.extend(item.get('display_paragraph',item['paragraph']) for item in audit['recommendations'])
        else:
            items=a.metadata.get('advice',{}).get('recommendations',[])
            advice.extend(item['paragraph'] for item in items)
            if not items:advice.extend(p for p in a.sections.get('advice','').splitlines() if p.strip())
    advice=list(dict.fromkeys(advice))
    for p in slots[43:]:
        if p.tag!='{'+st.NS['w']+'}sectPr':body.remove(p)
    prev,cur=comparison['previous_range'],comparison['current_range']
    caption=compact_paragraph(slots[43],f'指标对比  比较期：{prev[0]}至{prev[1]}；本期：{cur[0]}至{cur[1]}',size=18)
    pprop(caption,'keepNext','1');body.insert(len(body)-1,caption)
    comparison_panel=comparison_table(slots[23],comparison['rows'])
    body.insert(len(body)-1,comparison_panel)
    for text in advice:
        p=deepcopy(slots[43]);st.text(p,text)
        pp=pprop(p,'keepLines','1');pprop(p,'contextualSpacing','0');pprop(p,'snapToGrid','0')
        for n in pp.findall('w:numPr',st.NS):pp.remove(n)
        sp=pp.find('w:spacing',st.NS)
        if sp is None:sp=E.SubElement(pp,'{'+st.NS['w']+'}spacing')
        sp.attrib.clear()
        for k,v in {'before':'100','after':'100','line':'300','lineRule':'exact'}.items():sp.set('{'+st.NS['w']+'}'+k,v)
        for prop in p.xpath('.//w:rPr',namespaces=st.NS):
            for tag in ('sz','szCs'):
                el=prop.find('w:'+tag,st.NS)
                if el is None:el=E.SubElement(prop,'{'+st.NS['w']+'}'+tag)
                el.set('{'+st.NS['w']+'}val','21')
        body.insert(len(body)-1,p)
    for i,p in enumerate(root.xpath('//w:p',namespaces=st.NS)):
        p.set('{http://schemas.microsoft.com/office/word/2010/wordml}paraId',f'{0xA0300000+i:08X}')
    for table in (slots[23],slots[26],slots[29],slots[31]):
        if table.getparent() is body:center_report_table(table)
    center_report_table(comparison_panel)
    parts['word/document.xml']=st.serialize(root)
    with ZipFile(target,'w',ZIP_DEFLATED) as z:
        for name,raw in parts.items():z.writestr(name,raw)
    doc=Document(target)
    assert [[[c.text for c in row.cells] for row in table.rows[1:]] for table in doc.tables[1:1+len(expected)]]==expected
    visible=''.join(root.xpath('//w:t/text()',namespaces=st.NS))
    assert '资料说明' not in visible and '六项分别除以自身历史参考值' not in visible
    result.update(template_version=VERSION,layout='approved-physical-hangar-template',index_version=composite['version'],forecast_version=context.get('version'),scope='physical_hangar',hangar_display_order=order,advice_comparison=comparison)
    return result
