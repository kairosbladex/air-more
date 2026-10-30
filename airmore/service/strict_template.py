"""Fill the retained reference's real OOXML components without restyling them."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as ET
import matplotlib.pyplot as plt
from docx import Document

from .models import METRICS, TZ, survey_value_text

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
LABELS = dict(zip(METRICS, ('SO2', 'NO2', 'CO', 'O3', 'PM10', 'PM2.5')))
COLORS = {'so2':'#5b9bd5','no2':'#ed7d31','pm10':'#a5a5a5','pm2_5':'#ffc000','o3':'#4472c4','co':'#70ad47'}
TEMPLATE_VERSION = 'user-example-source/v3'
TEMPLATE = Path(__file__).parent / 'assets' / 'hangar-template.docx'


def xml(raw):
    return ET.fromstring(raw)


def serialize(root):
    return ET.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)


def text(element, value):
    """Replace text nodes only, retaining every source run/paragraph property."""
    nodes = element.xpath('.//w:t', namespaces=NS)
    if not nodes:
        p = element if element.tag == '{'+NS['w']+'}p' else element.find('w:p', NS)
        if p is None:
            p = ET.SubElement(element, '{'+NS['w']+'}p')
        run = ET.SubElement(p, '{'+NS['w']+'}r')
        nodes = [ET.SubElement(run, '{'+NS['w']+'}t')]
    target = next((n for n in nodes if n.text and n.text.strip()), nodes[0])
    for node in nodes:
        node.text = value if node is target else ''
        node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')


def png(fig):
    out = io.BytesIO()
    fig.savefig(out, format='png', dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out.getvalue()


def pie(record, runtime):
    order = ('pm10','pm2_5')
    with plt.rc_context({'font.family': runtime['font_family']}):
        fig, ax = plt.subplots(figsize=(3.3, 3.1))
        if record is None:
            ax.pie([1], colors=['#eeeeee'], wedgeprops={'edgecolor':'#bfbfbf','linewidth':.7})
            ax.text(0, 0, '可比资料不足\n暂无指数占比', ha='center', va='center', fontsize=11)
            handles = [plt.Line2D([0],[0],color=COLORS[m],linewidth=5) for m in order]
        else:
            ratios = record['ratios']
            if ratios is None:
                plt.close(fig)
                return pie(None, runtime)
            if set(ratios) != set(order) or any(not 0 <= v <= 1 for v in ratios.values()) or abs(sum(ratios.values())-1)>1e-6:
                raise ValueError('Invalid composite ratios')
            handles, *_ = ax.pie([ratios[m] for m in order], colors=[COLORS[m] for m in order],
                                autopct=lambda v:f'{v:.1f}%', textprops={'fontsize':11}, startangle=90)
        ax.legend(handles, [LABELS[m] for m in order], loc='lower center', bbox_to_anchor=(.5,-.04),
                  ncol=2, fontsize=9, frameon=False, handlelength=.8, columnspacing=.65)
        fig.subplots_adjust(left=.01,right=.99,top=.99,bottom=.07)
        return png(fig)


def trend(hangar, runtime):
    period = hangar['current']
    days = [(d,v) for d,v in hangar['days'].items() if period['start']<=d<=period['end']]
    groups = {}
    for m in METRICS:
        entries = [v['survey'][m] for _,v in days]
        units = {v['unit'] for v in entries if v['value'] is not None}
        if len(units) == 1 and None not in units:
            groups.setdefault(next(iter(units)), []).append((m, entries))
    with plt.rc_context({'font.family':runtime['font_family'],'axes.unicode_minus':False}):
        count = max(1, len(groups))
        fig, axes = plt.subplots(count, 1, figsize=(8.3, 2.2 * count + .7), squeeze=False, facecolor='#031426')
        for ax, (unit, series) in zip(axes[:,0], groups.items() or [('无可用单位', [])]):
            ax.set_facecolor('#031426')
            for m, entries in series:
                ax.plot(range(len(days)), [v['value'] if v['value'] is not None else float('nan') for v in entries],
                        color=COLORS[m], linewidth=1.7, marker='o', markersize=3, label=LABELS[m])
            ticks = sorted({round(i*(len(days)-1)/5) for i in range(6)})
            ax.set_xticks(ticks, [days[i][0][5:] for i in ticks], fontsize=9)
            ax.tick_params(colors='white', labelsize=9)
            ax.set_ylabel(unit, color='white', fontsize=9)
            ax.grid(axis='y', linestyle=':', alpha=.3)
            for spine in ax.spines.values():spine.set_color('#526478')
            if series:ax.legend(loc='upper center', bbox_to_anchor=(.5,1.28), ncol=6, frameon=False, labelcolor='white', fontsize=9)
        basis=' 平台日统计' if hangar.get('statistics_source') else ' 巡测统计'
        fig.suptitle(period['start']+' 至 '+period['end']+basis, color='white', fontsize=10)
        fig.subplots_adjust(left=.09, right=.94, top=.82, bottom=.11, hspace=.75)
        return png(fig)


def forecast(end, runtime):
    with plt.rc_context({'font.family':runtime['font_family']}):
        fig,ax=plt.subplots(figsize=(9,5.2));ax.axis('off');ax.set_xlim(0,1)
        for i in range(5):
            x=(i+.5)/5
            day=date.fromisoformat(end)+timedelta(days=i+1)
            if i==0:ax.axvspan(0,.2,color='#f3f3f3')
            for y,content,size in [(.91,day.strftime('%m/%d'),15),(.72,'AQI：/',14),(.55,'预报数据\n待接入',13),
                                   (.35,'首要污染物：/',10),(.19,'天气：/',10),(.07,'温度 / 风况：/',10)]:
                ax.text(x,y,content,ha='center',va='center',fontsize=size,color='#606060',transform=ax.transAxes)
        fig.subplots_adjust(left=.01,right=.99,top=.99,bottom=.01)
        return png(fig)


def render_document(stats, analyses, composite, cfg, output, runtime, *,
                    pie_renderer=None, trend_renderer=None, forecast_renderer=None):
    render_pie = pie_renderer or pie
    render_trend = trend_renderer or trend
    render_forecast = forecast_renderer or forecast
    with ZipFile(TEMPLATE) as archive:
        parts={n:archive.read(n) for n in archive.namelist()}
    root=xml(parts['word/document.xml']);body=root.find('w:body',NS);slots=list(body)
    request=stats['request'];start=date.fromisoformat(request['start_date']);end=date.fromisoformat(request['end_date'])
    text(slots[6], '空气质量研判'+{'DAY':'日报','WEEK':'周报','MONTH':'月报'}[request.get('report_type','MONTH')])
    no_metrics = not any(h['current']['survey'][m]['value'] is not None for h in stats['hangars'].values() for m in METRICS)
    text(slots[0], '合成数据测试，不用于业务决策' if stats['data_kind']=='synthetic' else ('数据状态报告：指标单位或有效数据不足，暂不作空气质量评价' if no_metrics else ''))
    cutoff = stats.get('provenance', {}).get('as_of') or request.get('as_of')
    text(slots[7], f'{end:%Y年%m月%d日}' + (f" 截至{datetime.fromisoformat(cutoff).astimezone(TZ):%H:%M}" if cutoff and datetime.fromisoformat(cutoff).astimezone(TZ).date() <= end else ''))
    meta=slots[11].findall('w:tr',NS)
    text(meta[0].findall('w:tc',NS)[1],cfg.company)
    text(meta[1].findall('w:tc',NS)[1],f'{start:%Y年%m月%d日} - {end:%Y年%m月%d日}')
    text(slots[22],f'{end.year}年{end.month}月{end.day}日各设备空气质量情况表')
    monthly=start.day==1 and (end+timedelta(days=1)).day==1 and start.month==end.month and start.year==end.year
    label=f'{start.year}年{start.month}月' if monthly else f'{start:%Y-%m-%d}至{end:%Y-%m-%d}'
    text(slots[25],label+'各设备空气质量情况表')
    expected_tables=[]
    for index,which in ((23,'last_day'),(26,'current')):
        expected=[]
        expected_tables.append(expected)
        table=slots[index];rows=table.findall('w:tr',NS);pattern=deepcopy(rows[1])
        text(rows[0].findall('w:tc',NS)[7], '巡测指数')
        for row in rows[1:]:table.remove(row)
        for hid,h in stats['hangars'].items():
            row=deepcopy(pattern);source=h['days'][request['end_date']] if which=='last_day' else h['current']
            values=[h['name']]+[survey_value_text(source['survey'][m]['value'],source['survey'][m]['unit']) for m in METRICS]
            cr=composite['hangars'][hid].get(which)
            if which == 'last_day' and request['report_type'] == 'DAY':
                cr=composite['hangars'][hid].get('current')
            values += [f"{cr['value']:.2f}" if cr and cr.get('value') is not None else '/', '/']
            expected.append(values)
            for cell,value in zip(row.findall('w:tc',NS),values):text(cell,value)
            table.append(row)
    # Use an existing blank paragraph for measurement and missing-value caveats.
    unit_sets={m:{h['current']['survey'][m]['unit'] for h in stats['hangars'].values() if h['current']['survey'][m]['value'] is not None and h['current']['survey'][m]['unit']} for m in METRICS}
    unit_note='；'.join(LABELS[m]+':'+('/'.join(sorted(unit_sets[m])) if unit_sets[m] else '待确认') for m in METRICS)
    if all(unit_sets[m]=={'mg/m³' if m=='co' else 'μg/m³'} for m in METRICS):
        unit_note='CO：mg/m³，其余：μg/m³'
    scope_note = '历史传感器复算，任务关联缺失，机库归属未确认。' if stats['data_kind']=='historical_sensor_replay' else ''
    text(slots[27], '注：'+unit_note+'。巡测指数为颗粒物内部相对指数；名次不计算，斜线表示不可计算、不表示零。'+scope_note)
    text(slots[28], '（二）各设备颗粒物巡测指数占比分析')
    for prop in list(slots[27].findall('w:pPr',NS)):slots[27].remove(prop)
    slots[27].insert(0,deepcopy(slots[25].find('w:pPr',NS)))
    # Copy original note typography from the existing 9pt caption; preserve slot placement.
    for run in slots[27].findall('w:r',NS):
        prop=run.find('w:rPr',NS)
        if prop is None:
            prop=ET.Element('{'+NS['w']+'}rPr');run.insert(0,prop)
        for tag in ('sz','szCs'):
            node=prop.find('w:'+tag,NS)
            if node is None:node=ET.SubElement(prop,'{'+NS['w']+'}'+tag)
            node.set('{'+NS['w']+'}val','16')
    rels=xml(parts['word/_rels/document.xml.rels']);image_count=0
    def replace_images(element, images, width=None):
        nonlocal image_count
        blips=element.xpath('.//a:blip',namespaces=NS)
        if len(blips)!=len(images):raise ValueError('Reference image slots changed')
        for blip,data in zip(blips,images):
            image_count+=1;rid=f'rIdReport{image_count}';name=f'media/report-{image_count}.png'
            blip.set('{'+NS['r']+'}embed',rid)
            ET.SubElement(rels,'{'+REL+'}Relationship',Id=rid,Type=NS['r']+'/image',Target=name)
            parts['word/'+name]=data
        # The sample contains screenshot crops and extents wider than the cells.
        # New charts use the original slots, fitted to their actual column width.
        for rectangle in element.xpath('.//a:srcRect', namespaces=NS):
            rectangle.getparent().remove(rectangle)
        if width:
            from PIL import Image
            for drawing, data in zip(element.xpath('.//w:drawing',namespaces=NS), images):
                with Image.open(io.BytesIO(data)) as im: height=int(width*im.height/im.width)
                for extent in drawing.xpath('.//wp:extent|.//a:xfrm/a:ext',namespaces=NS):
                    extent.set('cx',str(width));extent.set('cy',str(height))
    table=slots[29];rows=table.findall('w:tr',NS);patterns=[deepcopy(rows[0]),deepcopy(rows[1])]
    for row in rows:table.remove(row)
    for hid,h in stats['hangars'].items():
        heading,chart=map(deepcopy,patterns)
        text(heading.findall('w:tc',NS)[1],h['name'])
        text(chart, '对比期同条件观测占比                  本期可比观测占比')
        relative=h.get('relative', {})
        pair=[relative.get('previous'), relative.get('paired_current') or relative.get('current')]
        if pie_renderer is not None:
            pair=[composite['hangars'][hid].get('previous'), composite['hangars'][hid].get('current')]
        replace_images(chart,[render_pie(item,runtime) for item in pair], width=2160000)
        detail=f"对比期：{h['previous']['start']}至{h['previous']['end']}；本期：{start:%Y-%m-%d}至{end:%Y-%m-%d}。"
        if relative.get('current'):
            detail += f"可比层{relative['usable_strata']}/{relative['current_strata']}（{relative['coverage']:.2%}），仅代表这些观测层；占比不是污染源贡献。"
            if any(x['baseline_days']<14 for x in relative['strata']):detail += '采用短期历史参考。'
            change=relative.get('changes')
            if change:detail += f"相对{change['basis']}，同范围指数{change['previous_value']:.2f}→{change['current_value']:.2f}，净变化{change['net']:+.2f}点。"
        else:detail += '可比历史不足，暂不计算指数及占比。'
        paragraph=deepcopy(slots[43]);text(paragraph,detail)
        chart.findall('w:tc',NS)[-1].append(paragraph)
        table.extend([heading,chart])
    table=slots[31];rows=table.findall('w:tr',NS);pattern=deepcopy(rows[1])
    for row in rows[1:]:table.remove(row)
    for hid,h in stats['hangars'].items():
        row=deepcopy(pattern);cells=row.findall('w:tc',NS);text(cells[0],h['name'])
        text(cells[1], '')
        replace_images(row,[render_trend(h,runtime)], width=4350000)
        paragraph=deepcopy(slots[43]);text(paragraph,analyses[hid].sections['trend'])
        cells[1].append(paragraph);table.append(row)
    for i in range(3):text(slots[35+i],f'{end+timedelta(days=i+1):%m月%d日}：预报数据待接入。')
    replace_images(slots[40],[render_forecast(request['end_date'],runtime)], width=5100000)
    current=[h['current'] for h in stats['hangars'].values()]
    total=sum(h['samples'] for h in current)
    valid=sum(h['valid_aqi_days'] for h in current)
    expected=sum(h['days'] for h in current)
    advice=[paragraph for hid in stats['hangars'] for paragraph in analyses[hid].sections['advice'].split('\n') if paragraph.strip()]
    text(slots[43], advice[0] if advice else '模型建议待生成。')
    for offset, content in enumerate(advice[1:],1):
        paragraph=deepcopy(slots[43]);text(paragraph,content)
        body.insert(list(body).index(slots[43])+offset,paragraph)
    provenance = stats.get('provenance', {})
    observed = sum(h['observed_days'] for h in current)
    notes = [f"资料说明：本期有观测{observed}/{expected}个设备日，原始采样{provenance.get('raw_current_rows', total)}条，去重及质控后{total}条。日内有效分钟等权、区间内有数据日期等权；缺测不作插值。",
             '巡测结果仅代表采样覆盖，不作正式日AQI评价；当前未接入同期气象和正式预报资料，预报栏目保留缺项。']
    if stats['data_kind']=='historical_sensor_replay':
        notes.append('历史记录按项目和传感器归集，任务关联缺失，未确认机库归属。单位按本次核验的同项目产品配置和单位字典解释，历史配置未追溯确认；气体保留原始ppm或ppb，不作温压换算。')
    anchor=list(body).index(slots[43])+max(1,len(advice))
    for offset,note in enumerate(notes):
        paragraph=deepcopy(slots[43]);text(paragraph,note);body.insert(anchor+offset,paragraph)
    # Compact the source's caption without splitting a date across lines.
    for paragraph in slots[29].xpath('.//w:p[w:r/w:t]', namespaces=NS):
        if not ''.join(paragraph.xpath('.//w:t/text()',namespaces=NS)).startswith('对比期同条件'):continue
        for prop in paragraph.xpath('.//w:rPr',namespaces=NS):
            for tag in ('sz','szCs'):
                node=prop.find('w:'+tag,NS)
                if node is None:node=ET.SubElement(prop,'{'+NS['w']+'}'+tag)
                node.set('{'+NS['w']+'}val','16')
    # Keep the source cover metadata box within the page when converted by LO.
    position=slots[11].find('w:tblPr/w:tblpPr',NS)
    position.set('{'+NS['w']+'}vertAnchor','page')
    position.set('{'+NS['w']+'}tblpY','11200')
    # Source exact-height rows clip substituted Chinese fonts; retain minimum heights.
    for table in root.xpath('.//w:tbl',namespaces=NS):
        for height in table.xpath('.//w:trHeight',namespaces=NS):
            height.set('{'+NS['w']+'}hRule','atLeast')
        for paragraph in table.xpath('.//w:tc/w:p',namespaces=NS):
            pp=paragraph.find('w:pPr',NS)
            if pp is None:pp=ET.SubElement(paragraph,'{'+NS['w']+'}pPr')
            spacing=pp.find('w:spacing',NS)
            if spacing is None:spacing=ET.SubElement(pp,'{'+NS['w']+'}spacing')
            for k in ('before','after','beforeLines','afterLines'):spacing.set('{'+NS['w']+'}'+k,'0')
            for k in ('beforeAutospacing','afterAutospacing'):spacing.set('{'+NS['w']+'}'+k,'0')
            spacing.set('{'+NS['w']+'}line','240');spacing.set('{'+NS['w']+'}lineRule','auto')
            snap=pp.find('w:snapToGrid',NS)
            if snap is None:snap=ET.SubElement(pp,'{'+NS['w']+'}snapToGrid')
            snap.set('{'+NS['w']+'}val','0')
        for row in table.findall('w:tr',NS):
            rp=row.find('w:trPr',NS)
            if rp is None:rp=ET.SubElement(row,'{'+NS['w']+'}trPr')
            ET.SubElement(rp,'{'+NS['w']+'}cantSplit')
    # Six-character concentrations must fit the sample's narrow pollutant columns.
    # Preserve the original column grid and font, reducing only numeric-cell padding.
    for index in (23,26):
        for row in slots[index].findall('w:tr',NS)[1:]:
            for cell in row.findall('w:tc',NS)[1:]:
                prop=cell.find('w:tcPr',NS)
                if prop is None:
                    prop=ET.Element('{'+NS['w']+'}tcPr');cell.insert(0,prop)
                margins=prop.find('w:tcMar',NS)
                if margins is None:margins=ET.SubElement(prop,'{'+NS['w']+'}tcMar')
                for edge in ('left','right'):
                    node=margins.find('w:'+edge,NS)
                    if node is None:node=ET.SubElement(margins,'{'+NS['w']+'}'+edge)
                    node.set('{'+NS['w']+'}w','40');node.set('{'+NS['w']+'}type','dxa')
    # Multi-page floating tables in the sample detach headings in LibreOffice.
    # Keep their grids and components but let the repeated rows flow with headings.
    for index in (23,26,29,31):
        position=slots[index].find('w:tblPr/w:tblpPr',NS)
        if position is not None:position.getparent().remove(position)
    for index in (20,21,22,25,28,30,33,34,39,42):
        pp=slots[index].find('w:pPr',NS)
        if pp is None:pp=ET.SubElement(slots[index],'{'+NS['w']+'}pPr')
        keep=pp.find('w:keepNext',NS)
        if keep is None:keep=ET.SubElement(pp,'{'+NS['w']+'}keepNext')
        keep.set('{'+NS['w']+'}val','1')
    # Keep each table heading with its first data row; repeat column headers.
    for index in (23,26,29,31):
        rows=slots[index].findall('w:tr',NS)
        headings=rows[::2] if index==29 else rows[:1]
        if index!=29:
            rp=rows[0].find('w:trPr',NS)
            if rp is None:rp=ET.SubElement(rows[0],'{'+NS['w']+'}trPr')
            ET.SubElement(rp,'{'+NS['w']+'}tblHeader')
        for heading in headings:
            for paragraph in heading.xpath('.//w:p',namespaces=NS):
                pp=paragraph.find('w:pPr',NS)
                if pp is None:pp=ET.SubElement(paragraph,'{'+NS['w']+'}pPr')
                keep=pp.find('w:keepNext',NS)
                if keep is None:keep=ET.SubElement(pp,'{'+NS['w']+'}keepNext')
                keep.set('{'+NS['w']+'}val','1')
    # Unique drawing identifiers for cloned components.
    for i,node in enumerate(root.xpath('.//wp:docPr',namespaces=NS),1):node.set('id',str(i))
    parts['word/document.xml']=serialize(root);parts['word/_rels/document.xml.rels']=serialize(rels)
    target=output/'report.docx'
    with ZipFile(target,'w',ZIP_DEFLATED) as archive:
        for name,data in parts.items():archive.writestr(name,data)
    reread=Document(target)
    all_text=''.join(root.xpath('//w:t/text()',namespaces=NS))
    if '{{' in all_text or len(reread.tables)!=5:raise ValueError('Unfilled template slots')
    for table, expected in zip(reread.tables[1:3], expected_tables):
        if [[c.text for c in row.cells] for row in table.rows[1:]] != expected:
            raise ValueError('Table values do not match calculated facts')
    return {'template_version':TEMPLATE_VERSION,'files':['report.docx','report.pdf'],
            'template_sha256':hashlib.sha256(TEMPLATE.read_bytes()).hexdigest(),'image_count':image_count,
            'runtime':{k:v for k,v in runtime.items() if k!='env'},'readback':'passed'}
