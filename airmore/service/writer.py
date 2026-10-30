"""GPU-assisted evidence composition with no free-form factual escape hatch.

The model chooses and orders evidence-bound clauses per section. Each clause
contains the entire verified assertion (not just a reusable number). This keeps
a valid number from being attached to a fabricated cause, ranking or prediction.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Settings
from .models import LABELS, METRICS, ReportError, survey_value_text

PROMPT_VERSION = "hangar-evidence-composer/v1"
SECTIONS = ("overview", "trend", "advice")
TOKEN = re.compile(r"\{(F\d+)\}")


def fact_catalog(hangar: dict):
    facts, section_ids = {}, {s: [] for s in SECTIONS}
    def add(section, text):
        key = f"F{len(facts) + 1:03d}"
        facts[key] = text
        section_ids[section].append(key)
    current, previous = hangar["current"], hangar["previous"]
    add("overview", f"本期有效关联采样共{current['samples']}条，有采样日期{current['observed_days']}天，所选区间共{current['days']}天")
    add("overview", f"其中可计算日AQI的日期为{current['valid_aqi_days']}天；巡测统计仅代表实际采样覆盖")
    for metric in METRICS:
        a, b = current["survey"][metric], previous["survey"][metric]
        if a["value"] is not None:
            add("overview", f"本期{LABELS[metric]}巡测统计值为{survey_value_text(a['value'], a['unit'])} {a['unit']}，覆盖{a['days']}个有数据日")
        series = [(d, r["survey"][metric]) for d, r in hangar["days"].items()
                  if current["start"] <= d <= current["end"] and r["survey"][metric]["value"] is not None]
        if series and len({r["unit"] for _, r in series}) == 1:
            peak, record = max(series, key=lambda p: (p[1]["value"], p[0]))
            add("trend", f"本期已观测日期中，{LABELS[metric]}最高巡测日统计值为{survey_value_text(record['value'], record['unit'])} {record['unit']}，出现在{peak}")
        if (a["value"] is not None and b["value"] is not None and a["unit"] == b["unit"]
                and a["days"] == a["expected_days"] and b["days"] == b["expected_days"]):
            change = a["value"] - b["value"]
            direction = "增加" if change > 0 else "减少" if change < 0 else "变化"
            add("trend", f"与{previous['start']}至{previous['end']}的巡测统计相比，{LABELS[metric]}{direction}{survey_value_text(abs(change), a['unit'])} {a['unit']}；两期结果均仅代表实际采样")
    if not section_ids["trend"]:
        add("trend", "现有有效数据不足以描述指标变化，需补齐采样与单位信息后再分析")
    if current["observed_days"] < current["days"] or previous["observed_days"] < previous["days"]:
        add("advice", "建议先补齐缺测时段并核对采样连续性，再开展期间对比")
    if current["valid_aqi_days"] < current["days"]:
        add("advice", "建议核查监测位置、仪器口径和有效采样时长，避免用短时巡测替代完整日评价")
    add("advice", "建议结合原始记录开展复核；现有统计不足以判定污染来源，不据此作因果归因")
    return facts, section_ids


@dataclass
class WrittenAnalysis:
    sections: dict[str, str]
    templates: dict[str, list[str]]
    evidence: dict[str, str]
    metadata: dict


def validate_composition(body, facts, allowed):
    if not isinstance(body, dict) or set(body) != set(SECTIONS):
        raise ValueError("章节结构错误")
    sections = {}
    for section in SECTIONS:
        paragraphs = body[section]
        if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 3:
            raise ValueError("段落数量错误")
        seen, expanded = set(), []
        for template in paragraphs:
            if not isinstance(template, str) or len(template) > 600:
                raise ValueError("段落格式错误")
            ids = TOKEN.findall(template)
            residual = TOKEN.sub("", template)
            if not ids or len(ids) > 8 or re.sub(r"[，。；、\s]", "", residual):
                raise ValueError("只允许事实占位符及中文标点")
            if any(i not in allowed[section] or i in seen for i in ids) or len(ids) != len(set(ids)):
                raise ValueError("未知、跨章节或重复事实")
            seen.update(ids)
            expanded.append(TOKEN.sub(lambda m: facts[m[1]], template).strip().rstrip("，；、。") + "。")
        # Coverage and eligibility caveats cannot be omitted in favor of flattering facts.
        if section == "overview" and not set(allowed[section][:2]).issubset(seen):
            raise ValueError("缺少采样覆盖及AQI有效性说明")
        sections[section] = "\n".join(expanded)
    return sections


class OllamaAnalysisWriter:
    def __init__(self, settings: Settings, opener=None, runtime_probe=None):
        self.cfg = settings
        self.call_audit = []
        self.opener = opener or urllib.request.urlopen
        self.runtime_probe = runtime_probe or self._runtime_identity

    def _runtime_identity(self):
        try:
            with self.opener(urllib.request.Request(self.cfg.ollama_url + "/api/ps"), timeout=10) as response:
                running = json.loads(response.read(1024 * 1024))
            model = next(m for m in running["models"] if m["name"] == self.cfg.ollama_model)
            if not model.get("digest") or model.get("size_vram", 0) <= 0:
                raise ValueError("GPU residency unavailable")
            return {k: model.get(k) for k in ("digest", "size", "size_vram", "context_length")}
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            raise ReportError("MODEL_GPU_UNVERIFIED", "未能确认本次模型的GPU驻留状态") from None

    def write_report(self, stats):
        from .approved_advice import write_report
        self.call_audit = []
        self.advice_attempts = []
        return write_report(self, stats)

    def _request(self, payload):
        req = urllib.request.Request(self.cfg.ollama_url + "/api/chat",
                                     data=json.dumps(payload, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.opener(req, timeout=self.cfg.model_timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("模型响应过大")
        response = json.loads(raw)
        self.call_audit.append({'at': datetime.now(timezone.utc).isoformat(), 'model': response.get('model'),
                                'done': response.get('done'), 'done_reason': response.get('done_reason'),
                                'message': response.get('message')})
        return response

    def write(self, hangar: dict) -> WrittenAnalysis:
        self.call_audit = []
        analysis = self._write_selection(hangar)
        if hangar.get('evidence_cards'):
            from .report_advice import generate_advice
            try:
                content, audit = generate_advice(self, hangar)
            except (socket.timeout, TimeoutError):
                raise ReportError('MODEL_TIMEOUT', '模型建议生成超时，模型研判待生成') from None
            except (urllib.error.URLError, OSError):
                raise ReportError('MODEL_UNAVAILABLE', '模型建议服务不可用，模型研判待生成') from None
            analysis.sections['advice'] = content
            analysis.metadata['advice'] = audit
            analysis.metadata['call_audit'] = self.call_audit
            analysis.metadata['generated_at'] = datetime.now(timezone.utc).isoformat()
        return analysis

    def _write_selection(self, hangar: dict) -> WrittenAnalysis:
        facts, allowed = fact_catalog(hangar)
        base = "，".join("{" + i + "}" for i in allowed["overview"][:2])
        choices = {s: ["{" + i + "}。" for i in allowed[s]] for s in SECTIONS}
        choices["overview"] = [base + "。", *[base + "，{" + i + "}。" for i in allowed["overview"][2:]]]
        schema = {"type": "object", "additionalProperties": False, "required": list(SECTIONS),
                  "properties": {s: {"type": "string", "enum": choices[s]} for s in SECTIONS}}
        system = ("你是空气质量报告编辑。根据已有事实选择重点并组织overview、trend、advice三个章节。"
                  "每节只输出一个字符串，必须原样选取本节choices中的一个候选段落，不输出数组。"
                  "段落只能包含{F001}这种事实占位符及中文逗号、句号、分号。"
                  "必须输出合法JSON对象，键名为overview、trend、advice，值为候选字符串。不添加事实、原因、预测、排名。每节使用其允许的事实，不能跨节。"
                  "overview必须包含该节前两项，其余择要；避免重复。每个段落从该节choices选取，禁止展开占位符。")
        payload = {"model": self.cfg.ollama_model, "stream": False, "think": False, "format": schema,
                   "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 800}, "keep_alive": "5m",
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps({"facts": facts, "allowed": allowed, "choices": choices}, ensure_ascii=False)}]}
        started, timeout_retries, repairs, calls = time.monotonic(), 0, 0, 0
        while True:
            calls += 1
            try:
                response = self._request(payload)
            except (socket.timeout, TimeoutError):
                if timeout_retries == 0:
                    timeout_retries += 1
                    continue
                raise ReportError("MODEL_TIMEOUT", "GPU模型调用超时，重试后仍未完成") from None
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (socket.timeout, TimeoutError)) and timeout_retries == 0:
                    timeout_retries += 1
                    continue
                raise ReportError("MODEL_UNAVAILABLE", "GPU模型服务不可用") from None
            except (ValueError, OSError):
                raise ReportError("MODEL_RESPONSE_INVALID", "GPU模型响应无法解析") from None
            try:
                if (response.get("done") is not True or response.get("done_reason") != "stop"
                        or response.get("model") != self.cfg.ollama_model):
                    raise ValueError("模型响应未完整结束或模型身份不符")
                composition = json.loads(response["message"]["content"])
                if isinstance(composition, dict):
                    composition = {k: [v] if isinstance(v, str) else v for k, v in composition.items()}
                sections = validate_composition(composition, facts, allowed)
                return WrittenAnalysis(sections, composition, facts,
                                       {"engine": "ollama", "model": response["model"],
                                        "prompt_version": PROMPT_VERSION, "calls": calls,
                                        "timeout_retries": timeout_retries, "repairs": repairs,
                                        "elapsed_seconds": round(time.monotonic() - started, 3),
                                        **self.runtime_probe(),
                                        **{k: response.get(k) for k in ("eval_count", "eval_duration", "load_duration")}})
            except (ValueError, KeyError, TypeError, AttributeError):
                if repairs:
                    raise ReportError("MODEL_VALIDATION_FAILED", "模型输出未通过事实绑定校验，修复后仍失败") from None
                repairs += 1
                payload["messages"].append({"role": "user", "content":
                    '上次输出未通过JSON校验。只输出合法JSON对象，例如 {"overview":"{F001}，{F002}。","trend":"{F004}。","advice":"{F021}。"}，但实际占位符必须从本次choices中选择。三个章节各输出一个字符串，必须原样选取各自choices中的一个候选段落，不要输出数组，不展开事实占位符。'})


class TemplateAnalysisWriter:
    """Explicit offline mode, using the same fact validator as model writing."""
    def write_report(self, stats):
        from .approved_advice import write_report
        return write_report(None, stats)

    def write(self, hangar):
        facts, allowed = fact_catalog(hangar)
        body = {section: ["，".join("{" + key + "}" for key in allowed[section][:8]) + "。"]
                for section in SECTIONS}
        return WrittenAnalysis(validate_composition(body, facts, allowed), body, facts,
                               {"engine": "template", "version": "deterministic/v1", "calls": 0})
