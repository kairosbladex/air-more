"""One report-level set of original, fact-bound working recommendations."""
import json
import socket
import urllib.error

from .models import ReportError
from .writer import WrittenAnalysis
from .grounded_advice import generate, HORIZON
from .report_comparison import build_comparison
from .report_index import evaluate


def facts_for(stats):
    hs = stats['hangars']
    comparison = build_comparison(stats, stats.get('report_index') or evaluate(stats)) if hs else {'rows': [], 'leads': {}}
    particles = [row for row in comparison['rows'] if row['metric'] != 'index']
    comparable = [row for row in particles if row['percent'] is not None]
    focus = max(comparable, key=lambda row: abs(row['percent'])) if comparable else None
    if focus:
        name = '可吸入颗粒物' if focus['metric'] == 'pm10' else '细颗粒物'
        direction = '上升' if focus['percent'] > 0 else '下降' if focus['percent'] < 0 else '持平'
        particle_context = f'已有有效颗粒物周期对比，变化幅度较大的项目为{name}，方向为{direction}。'
    else:
        particle_context = '颗粒物缺少有效环比，先核对已有周期汇总与登记记录。'
    gaps = sorted({row['change_text'] for row in particles if row['delta'] is None})
    if gaps:
        particle_context += '另有不可比情形：' + '、'.join(gaps) + '。'
    # Exact identities and numbers stay in period_comparison and the program-bound
    # display leads. Action prose only needs the verified direction and gaps.

    context=stats.get('forecast',{})
    forecast={k:context.get(k) for k in ('city_name','issue_date','forecasts','weather','gaps')}
    kind=stats['request']['report_type']
    if kind in ('WEEK','MONTH'):
        # The figure may show the issued short-range forecast, but future-period
        # work advice must not reuse those weather details as next month's facts.
        forecast={'city_name':context.get('city_name'),'work_horizon':HORIZON[kind],
                  'required_action':'执行工作时，重新获取最新预报，与同一时次实测对照并保存差异记录。',
                  'weather_for_work_horizon':None}
    index_formed=stats.get('report_index',{}).get('available',False)
    return {
        'particles': particle_context+'变化仅表示平台对应周期统计差异，尚无对应现场污染来源调查结果。',
        'comparison': ('本期已形成综合指数排名，名次仅在所选机库内排序。' if index_formed else
                       '本次平台周期浓度、单位或有效指标不足，未形成有效综合指数排名。')+
                       '保持可比观测所需条件：同位置、同高度、相近时段及背景对照。',
        'forecast': json.dumps(forecast,ensure_ascii=False)+'。这是城市预报，独立于设备实测。后续工作按执行时最新预报调整。',
        # Data provenance remains in evidence.json. Recommendations discuss
        # operational checks, without exposing internal fixture annotations.
        'handover': '当前记录包含设备单位信息、采样时段和登记关联；校准状态及现场复测结果尚待核验。'}


def write_report(writer, stats):
    kind = stats['request']['report_type']
    facts = facts_for(stats)
    comparison = build_comparison(stats, stats.get('report_index') or evaluate(stats))
    if writer is None:
        horizon = HORIZON[kind]
        paragraphs = [f'{horizon}，值班人员先核对所选机库的原始监测记录及仪器状态，在相近位置、高度和时段安排复测，并保存背景对照。若差异再次出现，再结合现场记录确定下一步核查对象。',
                      f'{horizon}，研判人员核对最新预报与同一时次实测，根据实际变化调整复测时段，逐项记录安排依据和复核结果。',
                      '现场人员核实仪器单位、校准及采样记录，将监测时间和点位对应到原始资料；发现不一致时先复核记录，再更新统计结果。',
                      '下一轮排班前，明确复测点位、责任岗位和数据回收安排，交接尚待核查的事项，并根据复测结果调整后续工作。']
        audit = {'engine':'template','composition_mode':'explicit_offline_advice','facts':facts}
    else:
        try:
            audit = generate(writer, facts, kind, recommendation_count=4, advice_only=True, comparison_mode=True)
        except (socket.timeout, TimeoutError):
            raise ReportError('MODEL_TIMEOUT','工作建议生成超时，模型研判待生成') from None
        except (urllib.error.URLError, OSError):
            raise ReportError('MODEL_UNAVAILABLE','工作建议生成服务暂不可用') from None
        paragraphs = [r['paragraph'] for r in audit['recommendations']]
        if len(paragraphs) != 4:
            raise ReportError('MODEL_VALIDATION_FAILED','工作建议段落数量不符合报告模板')
        if any(any(t in p for t in ('资料说明','巡测结果','不是AQI','仅代表采样覆盖')) for p in paragraphs):
            raise ReportError('MODEL_VALIDATION_FAILED','工作建议包含非建议性资料说明')
        audit['engine'] = 'ollama'
        audit['call_audit'] = writer.call_audit
    audit['period_comparison'] = comparison
    if audit.get('recommendations'):
        for item in audit['recommendations']:
            item['display_paragraph'] = comparison['leads'].get(item['evidence_id'], '') + item['paragraph']
        paragraphs = [item['display_paragraph'] for item in audit['recommendations']]
    else:
        paragraphs = [comparison['leads'].get(key, '') + paragraph for key, paragraph in
                      zip(('particles','comparison','forecast','handover'), paragraphs)]
    result = {}
    for i,(hid,h) in enumerate(stats['hangars'].items()):
        result[hid] = WrittenAnalysis({'overview':'','trend':'','advice':'\n'.join(paragraphs) if i==0 else ''}, {}, facts,
                                      {'engine':audit['engine'],'report_advice':audit if i==0 else {}})
    return result
