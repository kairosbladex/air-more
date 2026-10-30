"""A 类：空气质量研判日报（政府日报版式，约 3 页）。

版式参照政府《环境空气质量研判日报》：三行报头 + 四个固定节；
定量部分参考灵嗅（Sniffer4D）监测报告风格（航迹着色图/单点位置图、
指标曲线、对比图、贡献率饼图）。
关键差异：浓度为观测时段等权均值，非全天 24h 均值；
综合指数仅供演示，非正式达标判定。
站点形态（无人机巡测 / 固定网格点）由 ctx.profile 控制文案与图件。
"""

from __future__ import annotations

import pandas as pd

from airmore.domain.composite import TABLE_FIELDS, composite_note, rank_color
from airmore.evidence.package import EvidenceItem
from airmore.reports import base
from airmore.reports.base import (ReportContext, add_event_evidence,
                                  add_met_evidence, add_qc_evidence,
                                  event_place, event_sentence, fmt_hm,
                                  fmt_num)
from airmore.render.docx_render import appendix_blocks

TITLE = "无人机低空环境空气质量研判日报"
TITLE_FIXED = "环境空气质量研判日报（固定网格点）"

STAT_HEADER = ["{date}", "SO2", "NO2", "CO", "O3", "PM10", "PM2.5",
               "综合指数", "名次"]


def _fmt_cell(field: str, v: float | None) -> str:
    """参考日报取值口径：μg/m³ 取整，CO（mg/m³）1 位小数，缺测 '/'。"""
    if v is None:
        return "/"
    return f"{v:.1f}" if field == "co" else f"{v:.0f}"


def _fmt_index(v: float | None) -> str:
    return "/" if v is None else f"{v:.2f}"


def _diffusion_desc(wind_speed: float) -> str:
    if wind_speed < 2.0:
        return "风速较小，大气扩散条件较差"
    if wind_speed < 3.5:
        return "风速中等，大气扩散条件一般"
    return "风速较大，大气扩散条件较好"


def _humidity_desc(humidity: float) -> str:
    if humidity >= 70:
        return "湿度较大，利于颗粒物吸湿增长"
    if humidity >= 40:
        return "湿度适中"
    return "湿度较低"


def build(ctx: ReportContext) -> dict:
    prof = ctx.profile
    title = TITLE if prof.kind == "drone" else TITLE_FIXED
    pkg = base.new_package(ctx, "A", title)
    pkg.charts = [str(p) for p in ctx.charts.values()]
    hi = ctx.hangar_index                      # {hid: {means, components, index, rank}}
    hangars = sorted(hi, key=lambda h: hi[h]["rank"])
    n = len(hangars)

    # ── 证据 ──
    # 综合指数演示口径限值（供全文数值校验与追溯）
    pkg.add(EvidenceItem(
        id=pkg.next_id(), kind="rule",
        description="综合指数演示口径：GB 3095-2012 二级 24h 限值"
                    "（SO2 150、NO2 80、O3 160、PM10 150、PM2.5 75 μg/m³，"
                    "CO 4 mg/m³），仅供演示，非正式达标判定",
        numbers=[{"value": 150.0, "unit": "μg/m³"},
                 {"value": 80.0, "unit": "μg/m³"},
                 {"value": 160.0, "unit": "μg/m³"},
                 {"value": 75.0, "unit": "μg/m³"},
                 {"value": 4.0, "unit": "mg/m³"}],
        source="domain.composite"))
    met_ids = add_met_evidence(pkg, ctx)
    qc_id = add_qc_evidence(pkg, ctx)
    event_ids = {id(e): add_event_evidence(pkg, e) for e in ctx.events}
    index_ids: dict[str, str] = {}
    for hid in hangars:
        eid = pkg.next_id()
        pkg.add(EvidenceItem(
            id=eid, kind="hangar_index",
            description=(f"{hid} 观测日综合指数 {_fmt_index(hi[hid]['index'])}"
                         f"（演示口径），名次 {hi[hid]['rank']}"),
            numbers=[], source="domain.composite（GB 3095-2012 二级 24h 限值）",
            extra={"means": hi[hid]["means"], "index": hi[hid]["index"],
                   "rank": hi[hid]["rank"]}))
        index_ids[hid] = eid

    # ── 核心结论（≤3 条）──
    best, worst = hangars[0], hangars[-1]
    pkg.conclusions.append({
        "text": (f"{ctx.date_label} 完成 {len(ctx.sortie)} 个{prof.sortie_label}观测，"
                 f"覆盖 {len(hangars)} 个{prof.hangar_label}；"
                 f"综合指数（演示口径）{best} 第 1（{_fmt_index(hi[best]['index'])}），"
                 f"{worst} 第 {n}（{_fmt_index(hi[worst]['index'])}）"
                 f"[{index_ids[best]}][{index_ids[worst]}]"),
        "evidence_ids": [index_ids[best], index_ids[worst]],
    })
    if ctx.events:
        top = max(ctx.events, key=lambda e: (e.duration_minutes, e.peak))
        pkg.conclusions.append({
            "text": event_sentence(top, event_ids[id(top)],
                                   place=event_place(ctx, top)) + "，建议复核",
            "evidence_ids": [event_ids[id(top)]],
        })
    pkg.conclusions.append({
        "text": f"{prof.period_label}均值不代表全天；业务筛查阈值不等于法定超标判定；"
                "预测因缺少连续历史与外部预报不可用",
        "evidence_ids": [qc_id],
    })

    pkg.suggested_actions = [
        "对异常时段（如有）安排复核，结合附近监测手段核实",
        "其余站点按常态化管控维持观测频次",
        "本次未覆盖时段与区域不作任何环境状况推断",
    ]
    pkg.data_limitations = [
        f"{prof.period_label}的观测不代表全天；"
        "综合指数与筛查阈值仅供演示，非正式达标判定",
    ]

    # ── 一、空气质量情况：统计表 ──
    stat_rows, row_colors = [], {}
    for i, hid in enumerate(hangars):
        means = hi[hid]["means"]
        stat_rows.append(
            [hid] + [_fmt_cell(f, means.get(f)) for f in TABLE_FIELDS]
            + [_fmt_index(hi[hid]["index"]), str(hi[hid]["rank"])])
        color = rank_color(hi[hid]["rank"], n)
        if color:
            row_colors[i] = color

    monthly_rows = [[hid] + ["/"] * 8 for hid in hangars]

    # ── 三、空气污染气象条件（定性，来自观测）──
    met = ctx.daily["fields"]
    ws = met["wind_speed"]["mean"]
    hum = met["humidity"]["mean"]
    pkg.narrative["空气污染气象条件"] = (
        f"{prof.period_label}（{pkg.period['start_full']} 至 "
        f"{pkg.period['end_full']}）"
        f"平均气温 {fmt_num(met['temperature']['mean'])} °C[{met_ids['temperature']}]，"
        f"相对湿度 {fmt_num(hum)} %RH[{met_ids['humidity']}]，"
        f"气压 {fmt_num(met['pressure']['mean'])} hPa[{met_ids['pressure']}]，"
        f"平均风速 {fmt_num(ws)} m/s[{met_ids['wind_speed']}]。"
        f"{_diffusion_desc(ws)}；{_humidity_desc(hum)}。"
        f"以上仅为{prof.period_label}观测，不代表全天气象条件。")

    # ── 四、工作建议（浓缩一段）──
    rank_txt = "；".join(
        f"{hid} 综合指数 {_fmt_index(hi[hid]['index'])}、第 {hi[hid]['rank']}"
        for hid in hangars)
    parts = [f"截至{base.zh_date(ctx.date_label)}观测，各{prof.hangar_label}观测日综合指数"
             f"（演示口径）与名次：{rank_txt}"
             f"[{''.join('][' + index_ids[h] for h in hangars)[2:]}]。"]
    if ctx.events:
        ranked = sorted(ctx.events, key=lambda e: -e.duration_minutes)
        ev_txt = "；".join(
            event_sentence(e, event_ids[id(e)], place=event_place(ctx, e))
            for e in ranked)
        parts.append(f"异常提示：{ev_txt}。")
        worst_status = ranked[0].status
        hangar_hit = ranked[0].hangar_id
        if prof.work_advice_kind == "fixed_point":
            parts.append(
                f"建议：{hangar_hit} 网格点按「{worst_status}」处置，"
                "24 小时内加密观测并结合附近地面监测站核实；"
                "其余时段常态化管控。")
        else:
            parts.append(
                f"建议：{hangar_hit} 异常航段按「{worst_status}」处置，"
                "24 小时内安排同航线复测、复测前检查传感器标定状态；"
                "其余机库常态化管控。")
    else:
        parts.append(f"各{prof.hangar_label}均未触发演示筛查阈值事件，建议常态化管控；"
                     "未触发不等于不存在风险。")
    parts.append("业务筛查阈值不等于法定超标判定，与官方气象预警等级无关。")
    pkg.narrative["工作建议"] = "".join(parts)

    span_days = (pd.Timestamp(pkg.period["end_full"][:10])
                 - pd.Timestamp(pkg.period["start_full"][:10])).days + 1
    pkg.narrative["预测说明"] = (
        f"当前仅有 {span_days} 天观测数据，无连续历史与外部气象预报，"
        "空气质量预测不可用；未来五天 AQI 数值预报无数据。"
        "缺乏数据不产出任何肯定性结论。")

    # ── 图件块（按站点形态）──
    if prof.chart_map == "site_map":
        map_block = {"type": "image", "path": ctx.charts["site_map"],
                     "width_cm": 8.5,
                     "caption": "观测网格点位置图（单一固定网格点）"}
    else:
        map_block = {"type": "image", "path": ctx.charts["trajectory"],
                     "width_cm": 10.0,
                     "caption": "巡测航迹与 PM2.5 分布（实际观测点，无观测区域留空）"}
    if n > 1:
        curve_block = {"type": "image_row",
                       "images": [{"path": ctx.charts["ts_pm2_5"], "width_cm": 8.6},
                                  {"path": ctx.charts["hangar_pm2_5"], "width_cm": 6.4}],
                       "caption": "左：PM2.5 观测曲线（含演示筛查阈值线）；"
                                  "右：分机库对比（航次等权）"}
    else:
        curve_block = {"type": "image", "path": ctx.charts["ts_pm2_5"],
                       "width_cm": 13.5,
                       "caption": "PM2.5 观测曲线（含演示筛查阈值线，按自然日分段）"}

    # ── DOCX body ──
    future_days = [(pd.Timestamp(ctx.date_label) + pd.Timedelta(days=k))
                   for k in range(1, 6)]
    if span_days > 1:
        s = pd.Timestamp(pkg.period["start_full"][:10])
        e = pd.Timestamp(pkg.period["end_full"][:10])
        first_cell = f"{s.month}.{s.day}–{e.month}.{e.day}"
    else:
        first_cell = base.dot_date(ctx.date_label)
    body = [
        {"type": "section", "text": prof.section1_title},
        {"type": "caption", "text": prof.table_caption},
        {"type": "table",
         "header": [first_cell] + STAT_HEADER[1:],
         "rows": stat_rows, "font_size": 9, "row_colors": row_colors,
         "col_widths": [3.4, 1.5, 1.5, 1.2, 1.5, 1.5, 1.5, 1.7, 1.3]},
        {"type": "note",
         "text": "注：排名后三字体为黄色，排名最后一位字体为红色"
                 "（排名对象少于 4 个时不着色）。"},
        {"type": "note",
         "text": f"注：数据口径为{prof.conc_desc}，非全天 24h 均值；"
                 "SO2/NO2/O3/PM10/PM2.5 单位 μg/m³（取整），CO 单位 mg/m³（1 位小数）；"
                 "缺测填“/”。"},
        {"type": "note", "text": f"注：{composite_note(prof.conc_desc)}。"},
        {"type": "caption", "text": "当月累计空气质量情况统计表"},
        {"type": "table",
         "header": [base.zh_month(ctx.date_label)] + STAT_HEADER[1:],
         "rows": monthly_rows, "font_size": 9,
         "col_widths": [3.0, 1.5, 1.5, 1.2, 1.5, 1.5, 1.5, 1.9, 1.3]},
        {"type": "note",
         "text": "注：当月累计需连续观测数据，当前仅有单周观测数据，暂不累计。"},
        map_block,
        curve_block,
        {"type": "image", "path": ctx.charts["pies"], "width_cm": 14.0,
         "caption": f"观测日六项污染物贡献率占比图（演示口径，分{prof.hangar_label}）"},

        {"type": "section", "text": "二、环境空气质量预测"},
        {"type": "heading", "text": "（一）空气质量形势预报"},
        {"type": "paragraph", "text": pkg.narrative["预测说明"]},
        {"type": "heading", "text": "（二）未来五天 AQI 数值预报"},
        {"type": "table", "header": ["日期", "AQI 数值预报"],
         "rows": [[f"{future_days[0].month}月{future_days[0].day}日–"
                   f"{future_days[-1].month}月{future_days[-1].day}日",
                   "无数据（无外部预报接入）"]],
         "font_size": 9, "col_widths": [6.0, 10.0]},

        {"type": "section", "text": "三、空气污染气象条件"},
        {"type": "paragraph", "text": pkg.narrative["空气污染气象条件"]},

        {"type": "section", "text": "四、工作建议"},
        {"type": "paragraph", "text": pkg.narrative["工作建议"]},
    ]
    body += appendix_blocks(base.qc_rows(ctx), base.rule_lines(ctx),
                            base.source_lines(ctx, pkg),
                            title="附：数据质量与规则说明",
                            disclaimer=prof.disclaimer,
                            disclaimer_suffix=prof.disclaimer_suffix)

    spec = {"header": base.make_header(ctx, title, "001"), "body": body,
            "disclaimer": prof.disclaimer}
    return base.finalize_report(ctx, pkg, spec, "A_巡测日报")
