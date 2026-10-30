"""GPU-authored period recommendations; failed or stale advice is never reused."""
import hashlib
import json
import re


def validate(body, kind):
    if not isinstance(body, dict) or set(body) != {'paragraphs'}:
        raise ValueError('建议结构必须为paragraphs')
    paragraphs = body['paragraphs']
    if not isinstance(paragraphs, list) or not 3 <= len(paragraphs) <= 4:
        raise ValueError('需要三至四段建议')
    if len(set(paragraphs)) != len(paragraphs):
        raise ValueError('建议重复')
    for p in paragraphs:
        if not isinstance(p, str) or not 50 <= len(p) <= 220:
            raise ValueError('每段建议限五十至二百二十字')
        numbers = re.sub(r'PM2\.5|PM10|SO2|NO2|O3|CO', '', p)
        numbers = re.sub(r'(?:不得|不能|不应|严禁)(?:直接|要求)?(?:判定为|补录)', '', numbers)
        forbidden = re.search(r'\d|%|https?://|公开数据|数据来源|外部网站|模型|GPU|已完成|治理成效|污染源为|主要来自|判定为|补录|降雨|雨后|天气|事实卡|鉴于背景浓度同步升高', numbers)
        if forbidden:
            raise ValueError('建议中不得出现此表述：' + forbidden.group())
        if (kind == 'WEEK' and '下月' in p) or (kind == 'MONTH' and '下周' in p):
            raise ValueError('建议周期与报告不符')
        if not any(word in p for word in ('复核', '复测', '核查', '比对', '核对', '对照')):
            raise ValueError('缺少可执行核查行动')
    return paragraphs


def generate(writer, facts, kind, period_hash):
    schema = {'type': 'object', 'required': ['paragraphs'], 'additionalProperties': False,
              'properties': {'paragraphs': {'type': 'array', 'minItems': 3, 'maxItems': 4,
                                            'items': {'type': 'string'}}}}
    horizon = {'DAY': '本班及下一轮', 'WEEK': '下周', 'MONTH': '下月'}[kind]
    system = ('撰写空气质量业务报告的工作建议，只输出JSON paragraphs数组，三至四段，每段约一百字。'
              '本次工作周期为' + horizon + '。只基于事实卡写未来可执行安排。'
              '每段明确责任岗位、具体行动、核查对象及复核或调整条件。'
              '分别覆盖臭氧复测与可能排放活动核查、颗粒物背景对照、数据质量和工作安排。事实卡没有气象记录，建议完全不要提天气或降雨。'
              '复测只能安排未来相近时段及点位，不能对已经过去的某一天重新观测。'
              '背景同步升高不能直接判定区域传输；只可建议结合更多证据核查局地或区域影响，不作污染成因结论。'
              '缺测不等于仪器故障；有原始记录才可核定后纳入，没有记录保留缺测。不得要求补录无记录数据。'
              '可以建议核查涉及溶剂使用、涂装、施工扬尘等活动，必须为拟核查对象而非认定已有污染源。'
              '不重复统计数字，不写任何阿拉伯数字、日期、百分比，污染物用中文名称。'
              '不写资料来源、网站、引用编号、模型说明、标题、列表符号。不得编造天气、已执行活动、企业名称或治理成效。'
              '避免加强监测、密切关注、多措并举等套话；不要预设改进成效。'
              '不要复述规则，不要提事实卡，不要机械写核查对象为或复核条件为，而应写自然连续的业务段落。'
              '输出结构必须为{"paragraphs":["第一段正文","第二段正文","第三段正文"]}，可有第四段，不得输出裸文本。')
    base = {'model': writer.cfg.ollama_model, 'stream': False, 'think': False,
            'keep_alive': '5m', 'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 1800}}
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(facts, ensure_ascii=False)}]
    attempts = []
    for _ in range(3):
        response = writer._request(base | {'format': schema, 'messages': messages})
        item = {'generation': response}; attempts.append(item)
        try:
            if response.get('done') is not True or response.get('done_reason') != 'stop' or response.get('model') != writer.cfg.ollama_model:
                raise ValueError('模型输出未完整结束')
            paragraphs = validate(json.loads(response['message']['content']), kind)
            audit = writer._request(base | {'options': base['options'] | {'num_predict': 400},
                'format': {'type': 'object', 'required': ['valid', 'reason'],
                           'properties': {'valid': {'type': 'boolean'}, 'reason': {'type': 'string'}}},
                'messages': [{'role': 'system', 'content': '审核环境报告建议的事实一致性、可执行性和周期一致性。拟安排的核查、责任岗位和条件性后续行动不是已经发生的事实，不能因此拒绝。拒绝虚构天气、具体污染来源、改善成效或无依据数值。只输出JSON valid和简短reason。'},
                             {'role': 'user', 'content': json.dumps({'facts': facts, 'horizon': horizon, 'paragraphs': paragraphs}, ensure_ascii=False)}]})
            item['audit'] = audit
            if audit.get('done') is not True or audit.get('done_reason') != 'stop' or audit.get('model') != writer.cfg.ollama_model or json.loads(audit['message']['content']).get('valid') is not True:
                raise ValueError('建议独立审核未通过：' + audit.get('message',{}).get('content',''))
            return {'kind': kind, 'period_facts_sha256': period_hash, 'model': writer.cfg.ollama_model,
                    'facts': facts, 'paragraphs': paragraphs, 'gpu': writer.runtime_probe(),
                    'validation': 'rules_and_independent_model_review_passed', 'attempts': attempts}
        except (ValueError, KeyError, TypeError) as exc:
            item['error'] = str(exc)
            messages.extend([{'role': 'assistant', 'content': response.get('message', {}).get('content', '')},
                             {'role': 'user', 'content': '请修订：' + str(exc) + '。仍只输出三至四段建议的JSON。'}])
    raise ValueError('GPU建议生成未通过核验：' + json.dumps(attempts, ensure_ascii=False))


def review_revision(writer, facts, kind, period_hash, paragraphs, draft_sha256):
    """Recheck edited GPU drafts; never retain an audit for changed wording."""
    validate({'paragraphs':paragraphs},kind)
    payload={'model':writer.cfg.ollama_model,'stream':False,'think':False,'keep_alive':'5m',
             'options':{'temperature':0,'num_ctx':8192,'num_predict':450},
             'format':{'type':'object','properties':{'valid':{'type':'boolean'},'reason':{'type':'string'}},'required':['valid','reason']},
             'messages':[{'role':'system','content':'独立审核环境报告建议是否符合事实、有具体可执行动作、周期一致。拟核查作业、岗位安排与条件性后续行动是建议，不是对已发生事实的认定。拒绝虚构已发生的天气、背景浓度变化、具体污染来源或治理成效。只输出JSON valid及简短reason。'},
                         {'role':'user','content':json.dumps({'facts':facts,'kind':kind,'paragraphs':paragraphs},ensure_ascii=False)}]}
    response=writer._request(payload)
    if response.get('done') is not True or response.get('done_reason')!='stop' or response.get('model')!=writer.cfg.ollama_model or json.loads(response['message']['content']).get('valid') is not True:
        raise ValueError('Edited advice review failed: '+json.dumps(response,ensure_ascii=False))
    return {'kind':kind,'period_facts_sha256':period_hash,'facts':facts,
            'model':writer.cfg.ollama_model,'gpu':writer.runtime_probe(),
            'paragraphs':paragraphs,'validation':'rules_and_independent_model_review_passed',
            'composition_mode':'gpu_draft_editorially_refined_and_gpu_reviewed',
            'original_draft_sha256':draft_sha256,'audit':response}
