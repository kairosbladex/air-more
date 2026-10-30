"""G 类：园区巡测与补测建议（专项保障，约 2–3 页）。

把 A/B 的已验证结论按"事项、依据、位置、建议时限、复核条件"整理成清单。
首期不自动下发飞行或处置命令。
"""

from __future__ import annotations

from airmore.reports import base
from airmore.reports.base import (ReportContext, add_event_evidence,
                                  add_met_evidence, add_qc_evidence,
                                  event_sentence, fmt_hm, fmt_num)
from airmore.render.docx_render import appendix_blocks
from airmore.rules.screening import STATUS_ORDER, STATUS_PRIORITY

TITLE = "园区巡测与补测建议"


def build(ctx: ReportContext) -> dict:
    pkg = base.new_package(ctx, "G", TITLE)
    pkg.charts = [str(ctx.charts["trajectory"]), str(ctx.charts["hangar_pm2_5"])]

    event_ids = {id(e): add_event_evidence(pkg, e) for e in ctx.events}
    met_ids = add_met_evidence(pkg, ctx)
    qc_id = add_qc_evidence(pkg, ctx)

    # ── 事项清单：事项 / 依据 / 位置 / 建议时限 / 复核条件 ──
    items: list[dict] = []
    ranked = sorted(ctx.events,
                    key=lambda e: (-STATUS_ORDER[e.status], -e.duration_minutes))
    for e in ranked:
        eid = event_ids[id(e)]
        urgent = e.status == STATUS_PRIORITY
        items.append({
            "事项": f"{e.label} 异常航段复测核查（内部状态「{e.status}」）",
            "依据": f"{event_sentence(e, eid)}",
            "位置": f"{e.hangar_id} {e.sortie_id} 航段（峰值位置 {e.peak_lat}, {e.peak_lon}）",
            "建议时限": "24 小时内" if urgent else "48 小时内",
            "复核条件": "同航线复测；复测前检查传感器零点与标定状态",
            "evidence_ids": [eid],
        })

    # 数据质量类事项（来自质控缺测记录）
    miss = [r for r in pkg.qc_records if r["check"] == "缺测"]
    if miss:
        fields = "、".join(sorted({r["detail"].split(" ")[0] for r in miss}))
        items.append({
            "事项": f"传感器缺测核查（字段 {fields}）",
            "依据": f"质控发现缺测 {sum(r['count'] for r in miss)} 条[{qc_id}]",
            "位置": "涉及航次对应传感器",
            "建议时限": "下次飞行前",
            "复核条件": "核查采样链路后补测同航线",
            "evidence_ids": [qc_id],
        })

    # 覆盖补充事项
    hangars = sorted({v["hangar_id"] for v in ctx.sortie.values()})
    items.append({
        "事项": "巡测覆盖补充",
        "依据": (f"本次巡测覆盖 {'、'.join(hangars)} 共 {len(ctx.sortie)} 个航次，"
                 f"观测时段 {fmt_hm(ctx.period_start)}–{fmt_hm(ctx.period_end)}[{qc_id}]"),
        "位置": "未覆盖时段与区域",
        "建议时限": "下一巡测日",
        "复核条件": "按常规计划补飞；未覆盖区域不作环境状况推断",
        "evidence_ids": [qc_id],
    })

    # ── 核心结论（≤3 条）──
    if ranked:
        top = ranked[0]
        pkg.conclusions.append({
            "text": f"首要事项：{event_sentence(top, event_ids[id(top)])}，"
                    "建议按清单时限安排复测",
            "evidence_ids": [event_ids[id(top)]],
        })
    pkg.conclusions.append({
        "text": f"本日共形成 {len(items)} 项保障事项，其中异常核查类 "
                f"{len(ranked)} 项、数据质量类 {1 if miss else 0} 项、覆盖补充类 1 项"
                f"[{qc_id}]",
        "evidence_ids": [qc_id],
    })
    pkg.conclusions.append({
        "text": "本清单为内部业务建议，不构成正式预警或处置命令",
        "evidence_ids": [qc_id],
    })

    pkg.suggested_actions = [i["事项"] + "（" + i["建议时限"] + "）" for i in items]
    pkg.data_limitations = [
        "建议基于单日巡测与演示筛查规则（仅供演示，非正式标准判定）；"
        "未观测时段与区域不作推断；首期不自动下发飞行或处置命令",
    ]

    pkg.narrative["保障背景"] = (
        f"{ctx.date_label} {fmt_hm(ctx.period_start)}–{fmt_hm(ctx.period_end)} "
        f"完成 {'、'.join(hangars)} 巡测。气象背景：风速均值 "
        f"{fmt_num(ctx.daily['fields']['wind_speed']['mean'])} m/s[{met_ids['wind_speed']}]，"
        f"温度均值 {fmt_num(ctx.daily['fields']['temperature']['mean'])} °C"
        f"[{met_ids['temperature']}]。以下事项依据当日观测与演示筛查规则形成。")

    pkg.narrative["事项说明"] = "；".join(
        f"{i['事项']}：{i['依据']}，{i['建议时限']}，复核条件：{i['复核条件']}"
        for i in items) + "。"

    item_rows = [[i["事项"], i["位置"], i["建议时限"], i["复核条件"],
                  "".join(f"[{e}]" for e in i["evidence_ids"])] for i in items]

    spec = {
        "header": {**base.make_header(
            ctx, "无人机低空环境园区巡测与补测建议", "G01"),
            "line1_size": 20},
        "body": [
            {"type": "meta_table", "rows": base.meta_rows(ctx, pkg, "演示版（未审核）"),
             "font_size": 9.5},
            {"type": "heading", "text": "核心结论"},
            {"type": "conclusions", "items": [c["text"] for c in pkg.conclusions]},
            {"type": "heading", "text": "观测位置与覆盖"},
            {"type": "image", "path": ctx.charts.get("trajectory"), "width_cm": 5.0,
             "caption": "位置图：事项涉及的巡测覆盖与异常航段位置"},
            {"type": "heading", "text": "建议动作"},
            {"type": "bullets", "items": pkg.suggested_actions},
            {"type": "heading", "text": "关键数据限制"},
            {"type": "paragraph", "text": pkg.data_limitations[0]},
            {"type": "page_break"},
            {"type": "section", "text": "事实与证据"},
            {"type": "heading", "text": "保障背景"},
            {"type": "paragraph", "text": pkg.narrative["保障背景"]},
            {"type": "heading", "text": "事项清单"},
            {"type": "table",
             "header": ["事项", "位置", "建议时限", "复核条件", "证据"],
             "rows": item_rows, "font_size": 8},
            {"type": "heading", "text": "事项说明"},
            {"type": "paragraph", "text": pkg.narrative["事项说明"]},
        ] + appendix_blocks(base.qc_rows(ctx), base.rule_lines(ctx),
                             base.source_lines(ctx, pkg)),
    }
    return base.finalize_report(ctx, pkg, spec, "G_专项建议")
