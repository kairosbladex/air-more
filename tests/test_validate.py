"""证据一致性校验测试：文本数值必须能在证据包中找到。"""

from airmore.evidence.package import EvidenceItem, EvidencePackage
from airmore.validate.numbers import (validate_package,
                                      validate_text_against_evidence)


def _pkg() -> EvidencePackage:
    pkg = EvidencePackage(report_type="A", report_id="A-TEST",
                          period={"start": "08:00", "end": "16:00"},
                          region="测试区域")
    pkg.add(EvidenceItem(
        id="E1", kind="statistic",
        description="PM2.5 航次等权日均值 42.5 μg/m³",
        numbers=[{"value": 42.5, "unit": "μg/m³"},
                 {"value": 75.0, "unit": "μg/m³"}],
        source="test"))
    pkg.add(EvidenceItem(
        id="E2", kind="event",
        description="持续 28 分钟",
        numbers=[{"value": 28.0, "unit": "分钟"}],
        source="test"))
    return pkg


def test_consistent_text_passes():
    pkg = _pkg()
    res = validate_text_against_evidence(
        "PM2.5 航次等权日均值 42.5 μg/m³[E1]，高于演示筛查阈值 75 μg/m³ 的片段"
        "持续 28 分钟[E2]。", pkg)
    assert res.ok, res.errors
    assert res.checked_pairs == 3
    assert res.checked_refs == 2


def test_wrong_number_fails():
    pkg = _pkg()
    res = validate_text_against_evidence("日均值 43.0 μg/m³[E1]。", pkg)
    assert not res.ok
    assert any("43.0" in e for e in res.errors)


def test_unknown_evidence_ref_fails():
    pkg = _pkg()
    res = validate_text_against_evidence("见证据[E9]。", pkg)
    assert not res.ok
    assert any("E9" in e for e in res.errors)


def test_number_without_unit_ignored():
    pkg = _pkg()
    res = validate_text_against_evidence(
        "2026-03-15 完成 5 个航次，报告编号 A-2026-03-15-DEMO-01。", pkg)
    assert res.ok, res.errors


def test_unit_matching_is_strict():
    pkg = _pkg()
    # 42.5 以 mg/m³ 出现不应通过（证据中是 μg/m³）
    res = validate_text_against_evidence("浓度 42.5 mg/m³。", pkg)
    assert not res.ok


def test_validate_package_covers_narrative_and_conclusions():
    pkg = _pkg()
    pkg.narrative["概述"] = "日均值 42.5 μg/m³[E1]。"
    pkg.conclusions.append({"text": "异常持续 28 分钟[E2]。",
                            "evidence_ids": ["E2"]})
    results = validate_package(pkg)
    assert all(r.ok for r in results)

    pkg.narrative["错误段"] = "日均值 99.9 μg/m³。"
    pkg.conclusions.append({"text": "引用缺失证据[E7]。", "evidence_ids": ["E7"]})
    results = validate_package(pkg)
    assert not all(r.ok for r in results)
