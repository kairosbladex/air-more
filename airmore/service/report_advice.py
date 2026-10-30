"""GPU-selected business recommendations with exact facts and constrained prose."""
import hashlib
import json
import re
from datetime import datetime, timezone
from .models import ReportError
from .report_facts import fact_pack
from .advice import HORIZONS

VERSION='meteorological-report-prose/v3'
BANNED=re.compile(r'加强监测|密切关注|多措并举|全面提升|确保|说明工作|说明比较|调整布点策略|可靠依据|我认为|我们|作为AI|作为人工智能|综上所述|核查清单|提交材料|以便不得|污染源为|排放增加|治理成效|已完成|已开展|已执行|逆温|预计.*(?:晴|雨|扩散)|确认.*无任务')


def validate(body, facts, kind):
    if not isinstance(body,dict) or set(body)!={'recommendations'}:raise ValueError('顶层结构错误')
    items=body['recommendations']
    if not isinstance(items,list) or not 2<=len(items)<=3:raise ValueError('需要两至三段建议')
    used=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'evidence_id','title','paragraph'}:raise ValueError('段落字段错误')
        title, paragraph=item['title'],item['paragraph']
        if not isinstance(title,str) or not 4<=len(title)<=18 or not isinstance(paragraph,str) or not 28<=len(paragraph)<=200:raise ValueError('标题或正文长度不符')
        ref=item['evidence_id']
        if ref not in facts or ref in used:raise ValueError('事实引用缺失或重复')
        used.add(ref)
        unbound=paragraph
        if re.search(r'[0-9０-９%{}<>]|[零一二三四五六七八九十百千万两]+(?:成|倍|天|日|次|米|小时|分钟)',unbound+title):raise ValueError('paragraph不应复述观测数字，事实由程序插入')
        if BANNED.search(unbound+title):raise ValueError('删除套话或自述表达：'+BANNED.search(unbound+title).group())
        if 'coverage' not in facts and any(t in unbound for t in ('缺测日期','无记录日期')):raise ValueError('覆盖完整，不应新增缺测问题')
        forbidden={'DAY':('下周','下月','周例会','月度会商'),'WEEK':('下月','月度会商'),'MONTH':('下周','周例会')}[kind]
        if any(term in unbound for term in forbidden):raise ValueError('工作周期错配')
        if not any(term in unbound for term in ('观测','复测','对照','会商','排查','比对','核实','核对','整理')):raise ValueError('缺少业务动作')
    return items


def generate_advice(writer,h):
    available=fact_pack(h)
    keys=[k for k in ('peak','coverage' if 'coverage' in available else 'comparison') if k in available]
    for k in ('reference','particles','scope'):
        if len(keys)<2 and k in available and k not in keys:keys.append(k)
    facts={k:available[k] for k in keys};horizon=HORIZONS[h['report_type']]
    from .report_actions import choices
    options=choices(facts,h['report_type'])
    schema={'type':'object','required':['selections'],'additionalProperties':False,'properties':{'selections':{'type':'array','minItems':len(facts),'maxItems':len(facts),'items':{'type':'string','enum':list(options)}}}}
    base={'model':writer.cfg.ollama_model,'stream':False,'think':False,'keep_alive':'5m','options':{'temperature':0,'num_ctx':8192,'num_predict':800}}
    system=('根据事实为气象局空气质量报告选择最合适的工作建议。每条事实选择一个对应候选，不重复同一事实。只返回单个JSON对象selections数组，数组元素为候选编号。'
            '比较候选对本期高值、对比范围或采样覆盖的针对性，选择能直接指导'+horizon+'的安排。不要返回正文、理由或其他字段。')
    payload=base|{'format':schema,'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps({'facts':facts,'choices':options,'output_example':{'selections':[next(k for k,v in options.items() if v['evidence_id']==fact) for fact in facts]}},ensure_ascii=False)}]}
    attempts=[];writer.advice_attempts=attempts
    for n in range(2):
        rec={'started_at':datetime.now(timezone.utc).isoformat(),'attempt':n+1};attempts.append(rec)
        response=writer._request(payload);rec['response']=response
        try:
            if response.get('done') is not True or response.get('done_reason')!='stop' or response.get('model')!=writer.cfg.ollama_model:raise ValueError('模型调用未完整结束')
            body=json.loads(response['message']['content'])
            if not isinstance(body,dict) or set(body)!={'selections'} or not isinstance(body['selections'],list) or len(body['selections'])!=len(facts) or any(not isinstance(k,str) or k not in options for k in body['selections']):raise ValueError('候选选择格式错误')
            items=validate({'recommendations':[options[k] for k in body['selections']]},facts,h['report_type'])
            expanded=[{'title':v['title'],'paragraph':facts[v['evidence_id']]+v['paragraph']} for v in items]
            audit=writer._request(base|{'format':{'type':'object','required':['valid','reason'],'additionalProperties':False,'properties':{'valid':{'type':'boolean'},'reason':{'type':'string'}}},'messages':[{'role':'system','content':'审核两段工作建议的事实一致性和语句。事实原文是已计算的巡测统计，不是区域评价。只检查是否新增了未提供的观测结论、地名、污染来源、天气判断，或把局部指数当全区域。未来调取地面站和气象资料属于工作安排，不要求这些资料已存在。无需建议给出完整气象分析。检查是否明显重复同一动作、有病句或自述写作过程。不得凭空指控未出现的内容。返回单个JSON对象，例如{"valid":true,"reason":"事实与建议相符，表述通顺"}。'},{'role':'user','content':json.dumps({'facts':facts,'paragraphs':expanded,'horizon':horizon},ensure_ascii=False)}]})
            rec['audit_response']=audit
            if audit.get('done') is not True or audit.get('done_reason')!='stop' or audit.get('model')!=writer.cfg.ollama_model or json.loads(audit['message']['content']).get('valid') is not True:raise ValueError('语义或文风审查未通过')
            prose='\n'.join(v['title']+'。'+v['paragraph'] for v in expanded)
            return prose,{'version':VERSION,'horizon':horizon,'facts_sha256':hashlib.sha256(json.dumps(facts,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'evidence':facts,'recommendations':items,'paragraphs':expanded,'composition_mode':'gpu_selection_of_evidence_specific_business_prose','choices':options,'selections':body['selections'],'attempts':attempts,'validation':'schema_and_rules_passed_model_review_accepted','gpu':writer.runtime_probe()}
        except (ValueError,KeyError,TypeError) as exc:
            rec['validation_error']=str(exc)
            payload['messages'] += [{'role':'assistant','content':response.get('message',{}).get('content','')},{'role':'user','content':'修正上一稿：'+str(exc)+'。只输出单个JSON对象selections数组，每条事实选一个对应候选编号。不要改写候选，不写正文。'}]
    raise ReportError('MODEL_VALIDATION_FAILED','模型建议未通过事实核验或文风审查，模型研判待生成')
