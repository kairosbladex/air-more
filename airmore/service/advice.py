"""Structured model recommendations anchored to complete, deterministic facts."""
import hashlib
import json
import re
from datetime import datetime, timezone

from .models import ReportError

VERSION = 'meteorological-advice/v1'
HORIZONS = {'DAY': '本班及下一轮观测', 'WEEK': '下周观测与周例会', 'MONTH': '下月观测与月度会商'}
BANNED = re.compile(r'加强监测|密切关注|多措并举|全面提升|筑牢|排放增加|污染源为|治理成效|逆温|明天.*(晴|雨|风|扩散)|已完成|已开展|已执行|替代缺失|替代数据|补报|插值|声明书|无缺测声明|反映区域|区域背景|上级台站|签字|复印件|确认缺测时段无')
FIELDS = ('evidence_id', 'action', 'purpose', 'verification')


def validate_advice(body, cards, report_type=None):
    if not isinstance(body, dict) or set(body) != {'recommendations'}:
        raise ValueError('建议结构错误')
    items = body['recommendations']
    if not isinstance(items, list) or not 2 <= len(items) <= 3:
        raise ValueError('需要两至三条建议')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != set(FIELDS):
            raise ValueError('建议字段错误')
        if item['evidence_id'] not in cards or item['evidence_id'] in seen:
            raise ValueError('依据不存在或重复')
        seen.add(item['evidence_id'])
        if not isinstance(item['purpose'], str) or not item['purpose'].startswith(('用于', '以便', '便于')) or '解决' in item['purpose']:
            raise ValueError('purpose只写用于何种后续工作，不推断已有错误或问题')
        for key in FIELDS[1:]:
            value = item[key]
            if not isinstance(value, str) or not 8 <= len(value) <= 120:
                raise ValueError('建议必须给出具体动作目的与复核方式')
            forbidden_period = {'DAY': ('周报','月报','下周','下月'), 'WEEK': ('月报','下月'), 'MONTH': ('周报','下周')}.get(report_type, ())
            if any(term in value for term in forbidden_period):
                raise ValueError('建议与报告周期不一致')
            if re.search(r'[0-9%{}<>]|[零一二三四五六七八九十百千万两]+(?:成|倍|天|日|次|米|小时|分钟)', value) or BANNED.search(value):
                raise ValueError('建议不得补写数字、因果或套话')
        if not any(v in item['action'] for v in ('核对', '核查', '核验', '复核', '复测', '对照', '对比', '比对', '整理', '保留', '补齐', '检查')):
            raise ValueError('缺少可执行核查动作')
    return items


def generate_advice(writer, hangar):
    full_date_coverage = hangar['current']['observed_days'] == hangar['current']['days']
    selected_cards = [c for c in hangar['evidence_cards'] if not (c['kind'] == 'coverage' and full_date_coverage)]
    if len(selected_cards) < 2:
        selected_cards = hangar['evidence_cards']
    cards = {c['id']: c['text'] for c in selected_cards}
    scopes = {
        'coverage': '核对原始采样记录与观测安排，区分未安排观测和记录缺失；整理覆盖清单供交接或会商。不能要求连续观测，也不能补报不存在的数据。',
        'level': '核对该指标原始记录、单位和统计时段；下一轮沿用相近时段与记录高度作对照。仅反映采样覆盖，不能判断区域空气质量或趋势。',
        'gap': '先核验两项颗粒物的浓度单位、原始位置与高度；整理同设备同条件的已有历史记录并标出缺口。不得用其他站点、其他设备、替代资料或推算值补齐指数。',
        'relative': '复核已匹配观测层的原始记录、设备口径和日统计；保留相同时段高度的对照条件供会商。不得作污染来源归因。',
        'repeat': '在已有偏高观测层保留同条件复测和相邻对照；整理偏高日期与有效日期供会商。不得把多条记录当作独立飞行。'}
    cycle_scope = {
        'DAY': '本班完成记录和单位核对；下一轮观测保持相近时段高度，结果用于值班交接。不要要求额外签字或纸质材料。',
        'WEEK': '下周优先保留相近时段高度的固定对照；在周例会核查新增资料能否支持期间对比。' + ('' if full_date_coverage else '核对本周无记录日期的观测安排。'),
        'MONTH': '下月保留固定时段、高度和设备口径，形成可比历史记录；将资料覆盖和单位核验结果列入月度会商，用于决定下月观测安排。'}[hangar['report_type']]
    boundaries = {c['id']: scopes[c['kind']] + cycle_scope for c in selected_cards}
    horizon = HORIZONS[hangar['report_type']]
    facts_hash = hashlib.sha256(json.dumps(cards, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    item_schema = {'type': 'object', 'additionalProperties': False, 'required': list(FIELDS),
                   'properties': {k: {'type': 'string', **({'enum': list(cards)} if k == 'evidence_id' else {'minLength':8,'maxLength':120})} for k in FIELDS}}
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['recommendations'], 'properties': {
        'recommendations': {'type': 'array', 'minItems': 2, 'maxItems': min(3,len(cards)), 'items': item_schema}}}
    system = ('你为气象局值班、观测和会商人员撰写工作建议。只输出JSON。每条选择一个不同evidence_id，'
              'action写具体动作和核对对象，不要只有核对二字；purpose写业务用途，verification写如何复核及提交什么材料。每个字段至少八个汉字，不超过八十个汉字。'
              'purpose必须以用于、以便或便于开头，只描述后续业务用途，不写解决某问题，不暗示原始数据有错。action必须以核对、核查、核验、复核、复测、对照、整理、保留、补齐或检查开头。不要复述事实，正文将自动插入。禁止写任何数量，包括中文数字。数字和事实由程序原文插入，不在建议字段复述数值、日期、次数、编号、污染物数字名。'
              '只能围绕引用证据规划动作，不新增观测结论、地名、测段、设备状态、污染来源、气象成因、天气预报或已完成工作。'
              '使用本测段、本时段、该指标等指代。证据无测段时不可虚构测段。'
              '没有重复异常证据就不能说异常持续，资料少时围绕质控核查、同条件对照和会商资料提出建议；'
              '避免加强监测、密切关注、多措并举等空话。建议应各有用途，不重复。'
              '不得拟造新的制度、签字流程、声明书、录入系统或业务任务。禁止签字页、复印件或纸质材料要求，提交简明核查清单即可。'
              '核查结果尚未知，只能核对是否存在，不能预设确认缺测时段无任务之类结果。不要说存在统计错误、依据不足或设备故障，只说核对这些事项。'
              '本报告只讨论'+horizon+'，不要混入其他周期。只有'+str(len(cards))+'条可用依据；每条依据至多引用一次。'
              '顶层必须为对象，且只包含recommendations这个键，值是两至三条建议对象的数组。'
              '严格输出形如 {"recommendations":[{"evidence_id":"coverage","action":"...","purpose":"...","verification":"..."}, ...]}。')
    base = {'model': writer.cfg.ollama_model, 'stream': False, 'think': False, 'keep_alive': '5m',
            'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 1800}}
    payload = base | {'format': schema, 'messages': [{'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps({'周期': hangar['report_type'], '工作时限': horizon, '事实': cards, '每条建议允许的业务范围': boundaries}, ensure_ascii=False)}]}
    attempts = []
    writer.advice_attempts = attempts
    for attempt in range(2):
        record = {'started_at': datetime.now(timezone.utc).isoformat(), 'attempt': attempt + 1}
        attempts.append(record)
        response = writer._request(payload)
        record['response'] = response
        try:
            if response.get('done') is not True or response.get('done_reason') != 'stop' or response.get('model') != writer.cfg.ollama_model:
                raise ValueError('模型身份或结束状态不符')
            body = json.loads(response['message']['content'])
            items = validate_advice(body, cards, hangar['report_type'])
            # A separate critical pass checks semantic grounding, beyond schema/numbers.
            audit_payload = base | {'format': {'type': 'object', 'required': ['valid', 'reason'], 'additionalProperties': False,
                'properties': {'valid': {'type': 'boolean'}, 'reason': {'type': 'string'}}},
                'messages': [{'role': 'system', 'content': '审查建议是否仅基于引用事实规划未来核查工作。拒绝新增事实、虚构地名天气或污染来源、错误因果、把缺测当正常、把多条记录当多次飞行。要求action、purpose、verification对应引用的evidence_id，且同一动作没有重复凑数。建议补查尚无的资料是允许的，不等于声称资料已存在。数字已由程序插入，不要求建议再写数值。仅输出合法JSON对象，例如{"valid":true,"reason":"符合事实范围"}。必须有大括号和双引号，禁止YAML、Markdown、解释段落。'},
                             {'role': 'user', 'content': json.dumps({'facts': cards, 'recommendations': items, 'horizon': horizon}, ensure_ascii=False)}]}
            audit = writer._request(audit_payload)
            record['audit_response'] = audit
            if audit.get('done') is not True or audit.get('done_reason') != 'stop' or audit.get('model') != writer.cfg.ollama_model:
                raise ValueError('核验调用不完整')
            verdict = json.loads(audit['message']['content'])
            if verdict.get('valid') is not True:
                raise ValueError('语义核验未通过')
            paragraphs = [f"{cards[v['evidence_id']]}建议{v['action'].removeprefix('建议').rstrip('。')}，{v['purpose'].rstrip('。')}。{v['verification'].rstrip('。')}。" for v in items]
            paragraphs.append('涉及无人机补测时，以适飞条件和业务审批通过为前提。')
            return '\n'.join(paragraphs), {'version': VERSION, 'horizon': horizon, 'facts_sha256': facts_hash,
                'recommendations': items, 'evidence': cards, 'validation': 'schema_and_rules_passed_model_review_accepted',
                'attempts': attempts, 'gpu': writer.runtime_probe()}
        except (KeyError, TypeError, ValueError) as exc:
            record['validation_error'] = str(exc)
            payload['messages'].append({'role': 'assistant', 'content': response.get('message', {}).get('content', '')})
            payload['messages'].append({'role': 'user', 'content': '上次校验不通过：'+str(exc)+'。最多三条建议，禁止四条或更多；不得重复同一核查动作。每个字段至少八个汉字。action须写明核对对象，不能仅写核对或核查。不要复述任何观测事实或数量。仅按每条建议允许的业务范围编写，禁止替代数据、复印件、签字等新的行政流程。输出recommendations对象。'})
    raise ReportError('MODEL_VALIDATION_FAILED', '模型建议未通过事实核验，模型研判待生成')
