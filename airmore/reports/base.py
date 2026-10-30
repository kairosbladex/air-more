"""报告构建共用组件：上下文、证据构造、校验与渲染。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from airmore import __version__
from airmore.domain.indicators import COMPUTED_POLLUTANTS, POLLUTANTS
from airmore.domain.profile import DRONE_PROFILE, SiteProfile
from airmore.evidence.package import EvidenceItem, EvidencePackage
from airmore.llm.writer import get_writer
from airmore.qc.checks import QCResult
from airmore.render.docx_render import render_report_docx
from airmore.render.pdf import docx_to_pdf, pdf_page_count
from airmore.rules.screening import Event, ThresholdConfig
from airmore.validate.numbers import validate_text_against_evidence

LOCAL_TZ = "Asia/Shanghai"  # 展示时区 UTC+8


@dataclass
class ReportContext:
    df: pd.DataFrame                      # 质控后数据
    qc: QCResult
    sortie: dict                          # stats.sortie_stats 结果
    daily: dict                           # stats.daily_summary 结果
    events: list[Event]
    cfg: ThresholdConfig
    charts: dict[str, Path]               # 图名 -> 文件
    out_dir: Path
    use_ollama: bool = False
    date_label: str = ""
    period_start: pd.Timestamp | None = None
    period_end: pd.Timestamp | None = None
    hangar_index: dict = field(default_factory=dict)  # 机库综合指数（演示口径）
    profile: SiteProfile = DRONE_PROFILE              # 站点形态（文案/图件参数化）


def fmt_num(v: float | None) -> str:
    """数值格式化：与证据包同源（1 位小数，去掉多余的 .0）。"""
    if v is None:
        return "无数据"
    return f"{float(v):g}"


def fmt_hm(ts: pd.Timestamp | str) -> str:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(LOCAL_TZ)
    return ts.strftime("%H:%M")


def zh_date(date_label: str) -> str:
    """2026-03-15 → 2026年3月15日"""
    y, m, d = (int(x) for x in date_label.split("-"))
    return f"{y}年{m}月{d}日"


def dot_date(date_label: str) -> str:
    """2026-03-15 → 2026.3.15（参考日报表头格式）"""
    y, m, d = (int(x) for x in date_label.split("-"))
    return f"{y}.{m}.{d}"


def zh_month(date_label: str) -> str:
    """2026-03-15 → 2026年3月"""
    y, m, _ = date_label.split("-")
    return f"{y}年{int(m)}月"


def make_header(ctx: ReportContext, line1: str, issue: str) -> dict:
    """政府日报式三行报头（期号为演示编号）。"""
    year = ctx.date_label.split("-")[0]
    return {
        "line1": line1,
        "line2": f"第﹝{year}﹞第{issue}期{ctx.profile.issue_note}",
        "line3": zh_date(ctx.date_label),
    }


def hangar_region(ctx: ReportContext) -> str:
    if ctx.profile.region:
        return ctx.profile.region
    hangars = "、".join(sorted({v["hangar_id"] for v in ctx.sortie.values()}))
    return f"{hangars} 周边巡测区域（虚构机库，演示区域）"


def _iso_local(ts: pd.Timestamp) -> str:
    return ts.tz_convert(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def new_package(ctx: ReportContext, report_type: str, title: str) -> EvidencePackage:
    pkg = EvidencePackage(
        report_type=report_type,
        report_id=f"{report_type}-{ctx.date_label}-DEMO-01",
        period={"start": fmt_hm(ctx.period_start), "end": fmt_hm(ctx.period_end),
                "date": ctx.date_label, "tz": "UTC+8",
                "start_full": _iso_local(ctx.period_start),
                "end_full": _iso_local(ctx.period_end)},
        region=hangar_region(ctx),
    )
    pkg.disclaimer = ctx.profile.disclaimer
    pkg.qc_records = ctx.qc.to_rows()
    pkg.rule_info = {
        "rule_version": ctx.cfg.version,
        "reference": ctx.cfg.reference,
        "disclaimer": ctx.cfg.disclaimer,
        "internal_status_note": ctx.cfg.internal_status_note,
        "persistence": ctx.cfg.persistence,
        "thresholds": ctx.cfg.pollutants,
    }
    # 规则证据：报告文本中允许出现的全部阈值与持续性参数
    p = ctx.cfg.persistence
    numbers = [{"value": float(th["threshold"]), "unit": th["unit"]}
               for th in ctx.cfg.pollutants.values()]
    numbers += [
        {"value": float(p["watch_minutes"]), "unit": "分钟"},
        {"value": float(p["review_minutes"]), "unit": "分钟"},
        {"value": float(p["priority_minutes"]), "unit": "分钟"},
        {"value": float(p["max_gap_minutes"]), "unit": "分钟"},
    ]
    pkg.add(EvidenceItem(
        id=pkg.next_id(), kind="rule",
        description="演示筛查阈值与持续性判定参数（仅供演示，非正式标准判定）",
        numbers=numbers, source=f"config/thresholds_demo.json（{ctx.cfg.version}）"))
    return pkg


def add_daily_evidence(pkg: EvidencePackage, ctx: ReportContext,
                       fields: list[str]) -> dict[str, str]:
    """把航次等权日均统计放入证据包，返回 field -> 证据编号。"""
    ids: dict[str, str] = {}
    for f in fields:
        d = ctx.daily["fields"].get(f)
        if not d or d["mean"] is None:
            continue
        ind = POLLUTANTS[f]
        eid = pkg.next_id()
        numbers = [{"value": d["mean"], "unit": ind.unit}]
        desc = (f"{ind.label} 航次等权日均值 {fmt_num(d['mean'])} {ind.unit}"
                f"（{d['sorties_contributing']} 个航次等权平均）")
        extra = {}
        if d.get("max") is not None:
            numbers.append({"value": d["max"], "unit": ind.unit})
            desc += (f"；全日峰值 {fmt_num(d['max'])} {ind.unit}，"
                     f"出现于 {d['max_sortie']} {fmt_hm(d['max_time'])}")
            extra = {"max_sortie": d["max_sortie"], "max_time": d["max_time"],
                     "max_lat": d.get("max_lat"), "max_lon": d.get("max_lon")}
        pkg.add(EvidenceItem(id=eid, kind="statistic", description=desc,
                             numbers=numbers,
                             source=f"stats.daily_summary（{ctx.daily['weighting']}）",
                             extra=extra))
        ids[f] = eid
    return ids


def add_event_evidence(pkg: EvidencePackage, event: Event) -> str:
    eid = pkg.next_id()
    pkg.add(EvidenceItem(
        id=eid, kind="event",
        description=(
            f"{event.label} 于 {event.sortie_id}（{event.hangar_id}）"
            f"{fmt_hm(event.start)}–{fmt_hm(event.end)} 连续高于演示筛查阈值 "
            f"{fmt_num(event.threshold)} {event.unit}，持续 {fmt_num(event.duration_minutes)} 分钟，"
            f"峰值 {fmt_num(event.peak)} {event.unit}（{fmt_hm(event.peak_time)}，"
            f"位置 {event.peak_lat}, {event.peak_lon}），"
            f"事件均值 {fmt_num(event.mean)} {event.unit}，"
            f"内部状态「{event.status}」"
        ),
        numbers=[
            {"value": event.threshold, "unit": event.unit},
            {"value": event.peak, "unit": event.unit},
            {"value": event.mean, "unit": event.unit},
            {"value": event.duration_minutes, "unit": "分钟"},
        ],
        source=f"rules.screening（规则版本 {event.rule_version}，{event.basis}）",
        extra={"sortie_id": event.sortie_id, "hangar_id": event.hangar_id,
               "start": event.start.isoformat(), "end": event.end.isoformat(),
               "peak_lat": event.peak_lat, "peak_lon": event.peak_lon,
               "samples": event.samples, "status": event.status},
    ))
    return eid


def add_met_evidence(pkg: EvidencePackage, ctx: ReportContext) -> dict[str, str]:
    """气象背景证据（温度/湿度/气压/风速均值）。"""
    unit_map = {"temperature": "°C", "humidity": "%RH",
                "pressure": "hPa", "wind_speed": "m/s"}
    label_map = {"temperature": "温度", "humidity": "相对湿度",
                 "pressure": "气压", "wind_speed": "风速"}
    ids: dict[str, str] = {}
    for f, unit in unit_map.items():
        d = ctx.daily["fields"].get(f)
        if not d or d["mean"] is None:
            continue
        eid = pkg.next_id()
        pkg.add(EvidenceItem(
            id=eid, kind="statistic",
            description=f"{label_map[f]}航次等权均值 {fmt_num(d['mean'])} {unit}",
            numbers=[{"value": d["mean"], "unit": unit}],
            source="stats.daily_summary（航次等权平均）"))
        ids[f] = eid
    return ids


def add_qc_evidence(pkg: EvidencePackage, ctx: ReportContext) -> str | None:
    warnings = [r for r in pkg.qc_records if r["severity"] == "warning"]
    eid = pkg.next_id()
    total = next((r for r in pkg.qc_records if r["check"] == "有效样本"), None)
    numbers = []
    desc_parts = []
    if total:
        desc_parts.append(f"质控后有效样本 {total['count']} 条")
    if warnings:
        desc_parts.append("；".join(w["detail"] for w in warnings))
    else:
        desc_parts.append("无缺测、重复或范围异常")
    pkg.add(EvidenceItem(id=eid, kind="qc",
                         description="。".join(desc_parts),
                         numbers=numbers, source="qc.checks"))
    return eid


def event_place(ctx: ReportContext, event: Event) -> str:
    """事件发生的统计单元描述（航段 / 自然日，按站点形态）。"""
    if ctx.profile.kind == "fixed_point":
        return f"{event.hangar_id} 网格点 {event.sortie_id}（自然日）"
    return f"{event.hangar_id} 的 {event.sortie_id} 航段"


def event_sentence(event: Event, eid: str, place: str | None = None) -> str:
    place = place or f"{event.hangar_id} 的 {event.sortie_id} 航段"
    return (
        f"{fmt_hm(event.start)}–{fmt_hm(event.end)}，{place} {event.label} "
        f"连续高于演示筛查阈值 "
        f"{fmt_num(event.threshold)} {event.unit}，持续 {fmt_num(event.duration_minutes)} 分钟，"
        f"峰值 {fmt_num(event.peak)} {event.unit}（出现于 {fmt_hm(event.peak_time)}）"
        f"[{eid}]，内部状态「{event.status}」（与官方预警无关）"
    )


def finalize_report(ctx: ReportContext, pkg: EvidencePackage, spec: dict,
                    stem: str) -> dict:
    """写作 → 数值校验 → 渲染 DOCX/JSON → 转 PDF。返回产物路径。"""
    # 1. 受限写作（默认模板；Ollama 可选且失败回退）
    writer = get_writer(ctx.use_ollama)
    notes = set()
    for key, instruction in list(pkg.narrative.items()):
        res = writer.write(instruction, pkg, {"render": pkg.narrative[key]})
        pkg.narrative[key] = res.text
        notes.add(res.note)
    pkg.writing_note = "；".join(sorted(notes))

    # 2. 数值一致性校验（叙述与结论）
    errors: list[str] = []
    checked = 0
    for text in list(pkg.narrative.values()) + [c["text"] for c in pkg.conclusions]:
        res = validate_text_against_evidence(text, pkg)
        checked += res.checked_pairs
        errors.extend(res.errors)
    for c in pkg.conclusions:
        for eid in c.get("evidence_ids", []):
            if eid not in pkg.ids():
                errors.append(f"结论引用了不存在的证据编号 [{eid}]")
    if errors:
        raise ValueError("数值一致性校验未通过：\n" + "\n".join(errors))
    pkg.narrative.setdefault(
        "_validation", f"通过：共校验 {checked} 处数值与证据包一致")

    # 3. 渲染
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = pkg.save(ctx.out_dir / f"{stem}.json")
    docx_path = render_report_docx(spec, ctx.out_dir / f"{stem}.docx")
    pdf_path = docx_to_pdf(docx_path, ctx.out_dir)
    return {"json": json_path, "docx": docx_path, "pdf": pdf_path,
            "pdf_pages": pdf_page_count(pdf_path)}


def qc_rows(ctx: ReportContext) -> list[list[str]]:
    return [[f"{r['check']}（{r['severity']}）", str(r["count"]), r["detail"]]
            for r in ctx.qc.to_rows()]


def rule_lines(ctx: ReportContext) -> list[str]:
    p = ctx.cfg.persistence
    thresholds = "；".join(
        f"{POLLUTANTS[f].label if f in POLLUTANTS else f} "
        f"{fmt_num(th['threshold'])} {th['unit']}"
        for f, th in ctx.cfg.pollutants.items())
    return [
        f"规则版本：{ctx.cfg.version}；参考：{ctx.cfg.reference}"
        "（仅供演示，非正式标准判定）",
        f"{ctx.cfg.internal_status_note}持续性参数：关注≥{p['watch_minutes']} 分钟，"
        f"建议复核≥{p['review_minutes']} 分钟，优先核查≥{p['priority_minutes']} 分钟"
        f"或峰值≥{p['peak_factor']:g} 倍阈值，间隔>{p['max_gap_minutes']} 分钟切段。",
        f"演示筛查阈值：{thresholds}。",
    ]


def source_lines(ctx: ReportContext, pkg: EvidencePackage) -> list[str]:
    n_files = len(ctx.df["source_file"].unique()) if "source_file" in ctx.df else 0
    return [
        f"{ctx.profile.source_desc.format(n=n_files)}；"
        f"统计口径：{ctx.profile.weighting_note}",
        f"写作方式：{pkg.writing_note}；系统版本：air-more {__version__}",
    ]


def meta_rows(ctx: ReportContext, pkg: EvidencePackage, status: str) -> list[tuple[str, str]]:
    if pkg.period.get("start_full", "")[:10] == pkg.period.get("end_full", "")[:10]:
        period_txt = (f"{ctx.date_label} {pkg.period['start']}–"
                      f"{pkg.period['end']}（UTC+8）")
    else:
        period_txt = (f"{pkg.period['start_full']} 至 "
                      f"{pkg.period['end_full']}（UTC+8）")
    return [
        ("报告编号", pkg.report_id),
        ("观测时段", period_txt),
        ("适用区域", pkg.region),
        ("版本状态", status),
        ("数据截止", f"{pkg.period.get('end_full', ctx.date_label)}（UTC+8）"),
    ]


def fields_present(ctx: ReportContext) -> list[str]:
    return [f for f in COMPUTED_POLLUTANTS
            if ctx.daily["fields"].get(f, {}).get("mean") is not None]
