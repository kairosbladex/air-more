"""结构化证据包：每条结论可追溯到证据编号、数值与来源。

证据包是 LLM/模板写作的唯一输入。所有数值由程序计算并放入证据包，
叙述文字只允许引用证据编号，不允许模型自行生成数字。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from airmore import DISCLAIMER, THRESHOLD_DISCLAIMER


@dataclass
class EvidenceItem:
    id: str                    # E1, E2, ...
    kind: str                  # statistic / event / qc / rule
    description: str           # 中文描述
    numbers: list[dict] = field(default_factory=list)  # [{"value":..,"unit":..}]
    source: str = ""           # 来源（统计口径 / 规则版本）
    extra: dict = field(default_factory=dict)


@dataclass
class EvidencePackage:
    report_type: str
    report_id: str
    period: dict               # {"start":..,"end":..}
    region: str
    items: list[EvidenceItem] = field(default_factory=list)
    conclusions: list[dict] = field(default_factory=list)  # {"text":..,"evidence_ids":[..]}
    suggested_actions: list[str] = field(default_factory=list)
    data_limitations: list[str] = field(default_factory=list)
    qc_records: list[dict] = field(default_factory=list)
    rule_info: dict = field(default_factory=dict)
    narrative: dict[str, str] = field(default_factory=dict)  # 段落标题 -> 文本
    charts: list[str] = field(default_factory=list)          # 图文件路径
    disclaimer: str = DISCLAIMER
    threshold_disclaimer: str = THRESHOLD_DISCLAIMER
    writing_note: str = "确定性模板写作（未调用外部模型）"

    def add(self, item: EvidenceItem) -> EvidenceItem:
        self.items.append(item)
        return item

    def next_id(self) -> str:
        return f"E{len(self.items) + 1}"

    def all_numbers(self) -> list[tuple[float, str]]:
        """证据包中全部 (数值, 单位)，供数值一致性校验。"""
        out: list[tuple[float, str]] = []
        for it in self.items:
            for n in it.numbers:
                out.append((float(n["value"]), n["unit"]))
        return out

    def ids(self) -> set[str]:
        return {it.id for it in self.items}

    def to_dict(self) -> dict:
        return {
            "report_type": self.report_type,
            "report_id": self.report_id,
            "disclaimer": self.disclaimer,
            "threshold_disclaimer": self.threshold_disclaimer,
            "period": self.period,
            "region": self.region,
            "writing_note": self.writing_note,
            "evidence": [
                {"id": it.id, "kind": it.kind, "description": it.description,
                 "numbers": it.numbers, "source": it.source, "extra": it.extra}
                for it in self.items
            ],
            "conclusions": self.conclusions,
            "suggested_actions": self.suggested_actions,
            "data_limitations": self.data_limitations,
            "qc_records": self.qc_records,
            "rule_info": self.rule_info,
            "narrative": self.narrative,
            "charts": self.charts,
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path
