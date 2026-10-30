"""数值一致性校验：报告文本中的每个"数值+单位"必须能在证据包中找到。

写作器（模板或 LLM）输出的叙述文字必须经过本校验才能进入交付稿；
缺证据的数值或引用不存在的证据编号都会判为不一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from airmore.evidence.package import EvidencePackage

# 受校验的单位集合（长单位在前，避免 "mm" 被 "m" 抢先匹配）
_UNITS = ["μg/m³", "mg/m³", "m/s", "%RH", "hPa", "ppm", "km", "mm",
          "°C", "分钟", "°", "m"]
# 数值前不允许紧跟字母/数字/小数点，避免把 "PM2.5" 中的 "2.5" 误判为数值
_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(-?\d+(?:\.\d+)?)\s*("
    + "|".join(re.escape(u) for u in _UNITS) + r")"
)
_EVIDENCE_REF = re.compile(r"\[(E\d+)\]")

_TOLERANCE = 0.051  # 证据值统一保留 1 位小数，文本同源


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    checked_pairs: int = 0
    checked_refs: int = 0


def validate_text_against_evidence(text: str, pkg: EvidencePackage) -> ValidationResult:
    errors: list[str] = []
    allowed = pkg.all_numbers()  # [(value, unit)]

    pairs = _PATTERN.findall(text)
    for raw, unit in pairs:
        value = float(raw)
        if not any(abs(value - v) <= _TOLERANCE and u == unit for v, u in allowed):
            errors.append(f"文本中的数值 {raw} {unit} 在证据包中不存在")

    refs = set(_EVIDENCE_REF.findall(text))
    known = pkg.ids()
    for ref in sorted(refs):
        if ref not in known:
            errors.append(f"文本引用了不存在的证据编号 [{ref}]")

    return ValidationResult(ok=not errors, errors=errors,
                            checked_pairs=len(pairs), checked_refs=len(refs))


def validate_package(pkg: EvidencePackage) -> list[ValidationResult]:
    """校验证据包内全部叙述段落，返回每段的校验结果。"""
    results = []
    for title, text in pkg.narrative.items():
        res = validate_text_against_evidence(text, pkg)
        if not res.ok:
            res.errors = [f"[{title}] {e}" for e in res.errors]
        results.append(res)
    # 结论同样校验
    for c in pkg.conclusions:
        res = validate_text_against_evidence(c["text"], pkg)
        for eid in c.get("evidence_ids", []):
            if eid not in pkg.ids():
                res.ok = False
                res.errors.append(f"结论引用了不存在的证据编号 [{eid}]")
        if not res.ok:
            res.errors = [f"[核心结论] {e}" for e in res.errors]
        results.append(res)
    return results
