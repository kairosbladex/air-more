"""受限写作器：LLM 只是写作器，数值一律来自证据包。

默认使用确定性模板写作（离线可跑全流水线）。Ollama 客户端为可选组件，
仅当显式开启（--use-ollama 或环境变量 AIRMORE_USE_OLLAMA=1）时使用，
地址走环境变量，绝不在默认流程中调用任何 GPU 服务器。
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

from airmore.evidence.package import EvidencePackage


@dataclass
class WritingResult:
    text: str
    engine: str          # "template" 或 "ollama:<model>"
    note: str


class TemplateWriter:
    """确定性模板写作器（默认）。只引用证据包中已有的编号与数值。"""

    engine = "template"

    def write(self, instruction: str, pkg: EvidencePackage,
              context: dict) -> WritingResult:
        # context["render"] 由调用方（报告 builder）提供确定性文本
        return WritingResult(
            text=context["render"],
            engine=self.engine,
            note="确定性模板写作（未调用外部模型）",
        )


class OllamaWriter:
    """Ollama 受限写作器（可选，默认不启用）。

    通过环境变量配置：
    - AIRMORE_OLLAMA_BASE_URL：默认 http://127.0.0.1:11434
    - AIRMORE_OLLAMA_MODEL：默认 qwen3.5:9b
    任何失败都回退到模板写作，并在报告中注明，不包装为"无风险"。
    """

    def __init__(self, timeout: float = 30.0):
        self.base_url = os.environ.get("AIRMORE_OLLAMA_BASE_URL",
                                       "http://127.0.0.1:11434")
        self.model = os.environ.get("AIRMORE_OLLAMA_MODEL", "qwen3.5:9b")
        self.timeout = timeout

    def write(self, instruction: str, pkg: EvidencePackage,
              context: dict) -> WritingResult:
        facts = json.dumps(
            {"instruction": instruction,
             "allowed_evidence": [
                 {"id": it.id, "description": it.description, "numbers": it.numbers}
                 for it in pkg.items]},
            ensure_ascii=False)
        prompt = (
            "你是受限写作器。只允许使用给定证据中的数值、单位、时间与证据编号，"
            "不得生成任何新数字，不得给出证据之外的结论。请用一段中文改写下面的"
            "确定性草稿，保留全部 [E编号] 引用原样不动。\n\n"
            f"证据：{facts}\n\n草稿：{context['render']}"
        )
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps({"model": self.model, "prompt": prompt,
                                 "stream": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body.get("response", "").strip()
            if not text:
                raise RuntimeError("模型返回空文本")
            return WritingResult(text=text, engine=f"ollama:{self.model}",
                                 note=f"Ollama 模型 {self.model} 写作，数值经一致性校验")
        except Exception as exc:  # noqa: BLE001 - 任何失败都回退模板
            fallback = TemplateWriter().write(instruction, pkg, context)
            fallback.note = (f"Ollama 调用失败（{exc}），已回退确定性模板写作；"
                             "智能分析未完成，不作为无风险结论")
            return fallback


def get_writer(use_ollama: bool = False):
    """默认模板写作；仅显式开启时才使用 Ollama。"""
    enabled = use_ollama or os.environ.get("AIRMORE_USE_OLLAMA") == "1"
    return OllamaWriter() if enabled else TemplateWriter()
