"""B 类：污染物异常观测与复核建议（异常快报，约 2 页）。"""

from __future__ import annotations

from airmore.reports import base
from airmore.reports.base import (ReportContext, add_event_evidence,
                                  add_qc_evidence, event_sentence, fmt_hm,
                                  fmt_num)
from airmore.render.docx_render import appendix_blocks
from airmore.rules.screening import STATUS_ORDER

TITLE = "污染物异常观测与复核建议"


def build(ctx: ReportContext) -> dict:
    pkg = base.new_package(ctx, "B", TITLE)
    pkg.charts = [str(ctx.charts["ts_pm2_5"]), str(ctx.charts["trajectory"])]

    event_ids = {id(e): add_event_evidence(pkg, e) for e in ctx.events}
    qc_id = add_qc_evidence(pkg, ctx)

    if not ctx.events:
        # 无事件也必须明确说明，不能默认解释为"无风险"
        pkg.conclusions.append({
            "text": f"{ctx.date_label} 巡测未触发任何演示筛查阈值事件[{qc_id}]；"
                    "未触发不等于不存在风险，未观测时段与区域不作推断",
            "evidence_ids": [qc_id],
        })
        pkg.narrative["事件概述"] = (
            f"{ctx.date_label} {fmt_hm(ctx.period_start)}–{fmt_hm(ctx.period_end)} "
            "巡测期间，各指标均未出现连续高于演示筛查阈值的片段"
            "（仅供演示，非正式标准判定）。")
    else:
        ranked = sorted(ctx.events,
                        key=lambda e: (-STATUS_ORDER[e.status], -e.duration_minutes))
        top = ranked[0]
        pkg.conclusions.append({
            "text": event_sentence(top, event_ids[id(top)]) + "，建议优先安排复测核查",
            "evidence_ids": [event_ids[id(top)]],
        })
        others = [e for e in ranked[1:2]]
        for e in others:
            pkg.conclusions.append({
                "text": event_sentence(e, event_ids[id(e)]),
                "evidence_ids": [event_ids[id(e)]],
            })
        pkg.conclusions.append({
            "text": "上述异常为内部业务状态，依据演示筛查规则（仅供演示，非正式标准判定），"
                    "不构成法定超标判定，与官方气象预警等级无关；"
                    "单次巡测不能认定污染来源，需现场核验",
            "evidence_ids": [event_ids[id(top)]],
        })

        lines = []
        for e in ranked:
            lines.append(event_sentence(e, event_ids[id(e)]) + "。")
        pkg.narrative["事件概述"] = "\n".join(lines)

    detail_parts = []
    for e in ctx.events:
        eid = event_ids[id(e)]
        detail_parts.append(
            f"{e.label} 事件[{eid}]：航次 {e.sortie_id}（{e.hangar_id}），"
            f"起止 {fmt_hm(e.start)}–{fmt_hm(e.end)}，持续 {fmt_num(e.duration_minutes)} 分钟，"
            f"峰值 {fmt_num(e.peak)} {e.unit}（{fmt_hm(e.peak_time)}，"
            f"位置 {e.peak_lat}, {e.peak_lon}），事件均值 {fmt_num(e.mean)} {e.unit}，"
            f"演示筛查阈值 {fmt_num(e.threshold)} {e.unit}。"
        )
    if detail_parts:
        detail_parts[-1] = detail_parts[-1] + "单点尖峰与传感器漂移需通过复测排除。"
    pkg.narrative["事件详情"] = "\n".join(detail_parts) if detail_parts else "无。"

    pkg.suggested_actions = [
        "优先核查：24 小时内对异常航段安排同航线复测，复测前检查传感器零点与标定状态",
        "复测结果与原观测一并复核；若复测未复现，按传感器异常流程处理",
        "CH4 等无阈值依据的线索不作为泄漏认定，需现场核验",
    ]
    pkg.data_limitations = [
        "业务筛查阈值不等于法定超标判定；未满足标准规定的测量方法和平均时段，"
        "本报告不生成正式 AQI 或达标结论",
    ]

    event_rows = [[
        e.label, e.sortie_id, e.hangar_id, f"{fmt_hm(e.start)}–{fmt_hm(e.end)}",
        f"{fmt_num(e.peak)} {e.unit}", f"{fmt_num(e.duration_minutes)} 分钟",
        e.status, f"[{event_ids[id(e)]}]",
    ] for e in ctx.events]

    spec = {
        "header": {**base.make_header(
            ctx, "无人机低空环境污染物异常观测与复核建议", "B01"),
            "line1_size": 20},
        "body": [
            {"type": "meta_table", "rows": base.meta_rows(ctx, pkg, "演示版（未审核）"),
             "font_size": 9.5},
            {"type": "heading", "text": "核心结论"},
            {"type": "conclusions", "items": [c["text"] for c in pkg.conclusions]},
            {"type": "heading", "text": "观测位置与覆盖"},
            {"type": "image", "path": ctx.charts.get("trajectory"), "width_cm": 5.0,
             "caption": "位置图：异常航段所处巡测覆盖范围"},
            {"type": "heading", "text": "建议动作"},
            {"type": "bullets", "items": pkg.suggested_actions},
            {"type": "heading", "text": "关键数据限制"},
            {"type": "paragraph", "text": pkg.data_limitations[0]},
            {"type": "page_break"},
            {"type": "section", "text": "事实与证据"},
            {"type": "heading", "text": "异常事件记录"},
        ] + ([{"type": "table",
               "header": ["指标", "航次", "机库", "起止时间", "峰值", "持续", "内部状态", "证据"],
               "rows": event_rows, "font_size": 8.5}] if event_rows else []) + [
            {"type": "image", "path": ctx.charts["ts_pm2_5"], "width_cm": 5.5,
             "caption": "PM2.5 观测曲线（红色虚线为演示筛查阈值）"},
            {"type": "heading", "text": "事件详情与规则来源"},
            {"type": "paragraph", "text": pkg.narrative["事件详情"]},
        ] + appendix_blocks(base.qc_rows(ctx), base.rule_lines(ctx),
                             base.source_lines(ctx, pkg)),
    }
    return base.finalize_report(ctx, pkg, spec, "B_异常快报")
