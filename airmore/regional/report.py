"""Deterministic presentation built solely from validated regional facts."""

from __future__ import annotations

from datetime import timedelta

from .bundle import Bundle, FIELDS, LABELS, require

KIND_LABELS = {"synthetic": "合成数据测试，不用于业务决策",
               "reference_transcription": "参考件转录回放，未经独立核定",
               "business_import": "业务结果导入，来源与口径见报告及证据包",
               "grid_model": "网格模型结果，非地面站实测，不用于业务决策"}


def value_text(r: dict | None) -> str:
    if not r or r["quality"] == "missing":
        return "/"
    if "rank" in r:
        return str(r["rank"])
    value = r["value"]
    if r["metric"] == "composite":
        return f"{value:.2f}"
    if r["metric"] == "co":
        return f"{value:.1f}"
    return f"{value:.0f}"


def fact_text(r: dict) -> str:
    if "rank" in r:
        return f"{r['start']}至{r['end']} {LABELS[r['metric']]}在{r['scope_name']}排名{value_text(r)}"
    unit = "" if r["unit"] == "1" else f" {r['unit']}"
    return f"{r['start']}至{r['end']} {LABELS[r['metric']]} {value_text(r)}{unit}"


def build_view(b: Bundle) -> dict:
    m = b.manifest
    stats = {(r["entity_id"], r["period"], r["metric"]): r for r in b.statistics}
    ranks = {(r["entity_id"], r["period"], r["metric"], r["scope_id"]): r for r in b.ranks}
    sources = {s["id"]: s for s in m["sources"]}
    citations = {r["id"]: f"E{i + 1}" for i, r in enumerate(
        b.statistics + b.ranks + b.contributions + b.forecasts + b.advice)}
    blocks, missing, charts = [], [], []

    def paragraph(content, kind="paragraph"):
        blocks.append({"type": kind, "text": content})

    def source_note(records):
        ids = sorted({r["source_id"] for r in records})
        if ids:
            paragraph("来源：" + "；".join(f"{i} {sources[i]['label']}" for i in ids), "note")

    def missing_note(section, reason):
        missing.append({"section": section, "reason": reason})

    paragraph("环境空气质量研判日报", "title")
    paragraph(f"第〔{b.report_day.year}〕第{m['issue']}期", "issue")
    paragraph(f"{b.report_day.year}年{b.report_day.month}月{b.report_day.day}日", "date")
    paragraph(m["region"]["name"] + "｜" + KIND_LABELS[m["data_kind"]], "note")
    paragraph("一、区域空气质量情况", "section")

    def rank_for(eid, period, metric="composite"):
        return ranks.get((eid, period, metric, m["table_scope_id"]))

    def entities(period):
        return sorted((s for s in b.stations if s["kind"] != "combined"), key=lambda s: (
            rank_for(s["id"], period) is None or rank_for(s["id"], period)["rank"] is None,
            (rank_for(s["id"], period) or {}).get("rank") or 0, s["order"], s["id"]))

    def stats_table(period):
        start, end = b.period(period)
        label = f"昨日空气质量情况统计表（{end}）" if period == "day" else f"当月累计空气质量情况统计表（{start}至{end}）"
        paragraph(label, "caption")
        rows, styles, records, refs = [], [], [], []
        for s in entities(period):
            values = [stats.get((s["id"], period, f)) for f in (*FIELDS, "composite")]
            rank = rank_for(s["id"], period)
            rows.append([s["name"], *map(value_text, values), value_text(rank)])
            refs.append([s["id"], *[r["id"] if r else None for r in values], rank["id"] if rank else None])
            color = None
            if rank and rank["rank"] and rank["total"] and rank["total"] >= 4:
                if rank["rank"] == rank["total"]:
                    color = "FF0000"
                elif rank["rank"] > rank["total"] - 3:
                    color = "806000" if s["focus"] else "C09000"
            styles.append({"fill": "FFFF00" if s["focus"] else None, "color": color})
            for f, r in zip((*FIELDS, "composite", "rank"), [*values, rank]):
                if not r or r["quality"] == "missing":
                    missing_note(f"{period}/{s['id']}/{f}", r["reason"] if r else "未提供")
            records.extend(r for r in [*values, rank] if r)
        blocks.append({"type": "table", "header": ["区域/站点", *[LABELS[f] for f in FIELDS], "综合指数", "名次"],
                       "rows": rows, "styles": styles, "refs": refs,
                       "widths": [3.15, 1.3, 1.3, 1.1, 1.3, 1.3, 1.4, 1.8, 1.0]})
        paragraph("注：浓度单位为 μg/m³，CO 为 mg/m³；综合指数及名次采用导入结果。“/”表示未提供或缺测。", "note")
        scope_rows = [r for r in records if "scope_id" in r]
        if scope_rows:
            paragraph("排名范围：" + "；".join(sorted({r["scope_name"] for r in scope_rows}))
                      + "。重点园区为黄色底色；已知参评总数时，后三名黄字、末位红字。", "note")
        paragraph("统计口径：" + "；".join(sorted({r["basis"] for r in records}) or ["未提供"]), "note")
        source_note(records)

    stats_table("day")
    focus = [s for s in b.stations if s["focus"]]
    paragraph("重点园区昨日单项排名", "caption")
    rows, rank_refs = [], []
    for s in focus:
        rs = [rank_for(s["id"], "day", f) for f in FIELDS]
        rows.append([s["name"], *map(value_text, rs)])
        rank_refs.append([s["id"], *[r["id"] if r else None for r in rs]])
        for f, r in zip(FIELDS, rs):
            if not r or r["quality"] == "missing":
                missing_note(f"day/{s['id']}/{f}/rank", r["reason"] if r else "未提供")
    if rows:
        blocks.append({"type": "table", "header": ["重点园区", *[LABELS[f] for f in FIELDS]],
                       "rows": rows, "refs": rank_refs, "styles": [], "widths": [3.65] + [1.5] * 6})
        paragraph("比较范围使用本期统计表所配置的排名范围；其他范围名次见工作建议。", "note")
    else:
        paragraph("未配置重点园区。")
    stats_table("month")

    for s in focus:
        paragraph(f"昨日、当月六项污染物贡献率占比图（{s['name']}）", "caption")
        panels = []
        for period in ("day", "month"):
            rs = [r for r in b.contributions if r["entity_id"] == s["id"] and r["period"] == period]
            complete = len(rs) == 6 and all(r["quality"] == "valid" for r in rs)
            total = sum(r["value"] for r in rs) if complete else 0
            ratios = ({r["metric"]: r["value"] / total for r in rs} if total > 0 else None)
            reason = "；".join(sorted({r["reason"] for r in rs if r["quality"] == "missing"}))
            reason = reason or ("六项贡献率未完整提供" if not complete else "六项合计为零")
            if ratios is None:
                missing_note(f"{period}/{s['id']}/contribution", reason)
            panels.append({"period": period, "ratios": ratios, "reason": reason if ratios is None else "",
                           "refs": [r["id"] for r in rs]})
        key = f"contribution-{s['id']}"
        charts.append({"id": key, "entity_id": s["id"], "panels": panels})
        blocks.append({"type": "chart", "id": key})
        source_note([r for r in b.contributions if r["entity_id"] == s["id"]])

    paragraph("二、环境空气质量预测", "section")
    paragraph("（一）空气质量形势预报", "heading")
    forecast_by_day = {r["date"]: r for r in b.forecasts}

    def range_text(r, stem, unit):
        lo, hi = r.get(stem + "_min"), r.get(stem + "_max")
        return "未提供" if lo is None else f"{lo:g}–{hi:g}{unit}"

    forecast_rows, forecast_refs = [], []
    for delta in range(5):
        dt = (b.report_day + timedelta(days=delta)).isoformat()
        r = forecast_by_day.get(dt)
        if not r or r["quality"] == "missing":
            reason = r["reason"] if r else "未提供有效预报"
            if delta < 3:
                paragraph(f"{dt}：{reason}。")
            forecast_rows.append([dt, *["未提供"] * 7])
            missing_note(f"forecast/{dt}", reason)
        else:
            air = range_text(r, "aqi", "")
            temp, humidity = range_text(r, "temp", "℃"), range_text(r, "humidity", "%")
            wind = (r["wind_direction"] or "风向未提供") + " " + range_text(r, "wind", "级")
            pollutants = "、".join(LABELS[f] for f in r["primary_pollutants"]) or "未提供"
            forecast_rows.append([dt, air, r["category"] or "未提供", pollutants,
                                  r["weather"] or "未提供", temp, humidity, wind])
            if delta < 3:
                paragraph(f"{dt}，{r['weather'] or '天气未提供'}，气温{temp}，相对湿度{humidity}，"
                          f"{wind}。空气质量{r['category'] or '等级未提供'}，AQI：{air}，"
                          f"首要污染物：{pollutants}。[{citations[r['id']]}]"
                          + (f" 数据说明：{r['reason']}。" if r.get("reason") else ""))
            if r.get("reason"):
                missing_note(f"forecast/{dt}", r["reason"])
        forecast_refs.append(r["id"] if r else None)
    paragraph("（二）未来五天 AQI 数值预报", "heading")
    # Five date columns preserve the reference's compact forecast strip.
    labels = ["日期", "AQI", "空气质量", "首要污染物", "天气", "气温", "相对湿度", "风向风力"]
    blocks.append({"type": "table", "header": ["项目", *[r[0] for r in forecast_rows]],
                   "rows": [[labels[i], *[r[i] for r in forecast_rows]] for i in range(1, 8)],
                   "refs": forecast_refs, "styles": [], "widths": [1.65] + [2.2] * 5})
    source_note(b.forecasts)
    if b.forecasts:
        paragraph("预报口径：" + "；".join(sorted({r["basis"] for r in b.forecasts})), "note")
    paragraph("三、空气污染气象条件", "section")
    if b.images:
        for i in range(0, len(b.images), 2):
            pair = b.images[i:i + 2]
            blocks.append({"type": "weather", "ids": [r["id"] for r in pair],
                           "captions": [f"{r['caption']}；有效时段：{r['valid_start']}至{r['valid_end']}；"
                                        f"发布时间：{r['issued_at']}；来源：{sources[r['source_id']]['label']}。"
                                        for r in pair]})
    else:
        paragraph("未提供本期有效的空气污染气象预报图，气象研判资料待补充。")
        missing_note("weather_images", "未提供")

    paragraph("四、工作建议", "section")
    fact_map = {r["id"]: r for r in b.statistics + b.ranks}
    advice_map = {r["entity_id"]: r for r in b.advice}
    for s in [v for v in b.stations if v["focus"] or v["kind"] == "combined"]:
        facts = [r for r in b.statistics + b.ranks if r["entity_id"] == s["id"]
                 and r["metric"] == "composite" and r["quality"] == "valid"]
        summaries = []
        for period in ("day", "month"):
            period_facts = [r for r in facts if r["period"] == period]
            start, end = b.period(period)
            lead = f"昨日（{end}）" if period == "day" else f"当月（{start}至{end}）"
            values = [f"{r['scope_name']}导入名次{value_text(r)}" if "rank" in r
                      else f"综合指数{value_text(r)}" for r in period_facts]
            summaries.append(lead + ("、".join(values) or "综合指数及排名未提供")
                             + ("[" + ",".join(citations[r["id"]] for r in period_facts) + "]" if period_facts else ""))
        paragraph(s["name"] + "：" + "；".join(summaries) + "。")
        advice = advice_map.get(s["id"])
        if advice and advice["quality"] == "valid":
            content = advice["text"]
            for rid in advice["evidence_ids"]:
                content = content.replace("{" + rid + "}", fact_text(fact_map[rid]))
            paragraph("业务建议：" + content + f"（来源：{sources[advice['source_id']]['label']}）")
        else:
            reason = advice["reason"] if advice else "未提供"
            paragraph("业务建议待补充：" + reason + "。")
            missing_note(f"advice/{s['id']}", reason)
    if not focus and not any(s["kind"] == "combined" for s in b.stations):
        paragraph("未配置重点分析对象，业务建议待补充。")

    paragraph("附：数据来源与缺失说明", "section")
    for source in sources.values():
        paragraph(f"{source['id']}：{source['label']}；发布/核定时间：{source['issued_at']}。", "note")
    paragraph("综合指数、排名及预报均采用导入结果；本系统未重新核定其业务口径。", "note")
    # Group missing fields to keep long station lists from producing huge appendices.
    grouped = {}
    for item in missing:
        section = item["section"].split("/")
        key = "/".join(section[:2]) if len(section) > 2 else item["section"]
        grouped.setdefault((key, item["reason"]), []).append(section[-1])
    compact = {}
    station_names = {s["id"]: s["name"] for s in b.stations}
    period_names = {"day": "昨日", "month": "当月", "forecast": "预报", "advice": "业务建议"}
    field_names = LABELS | {"rank": "名次", "contribution": "贡献率"}
    for (key, reason), fields in grouped.items():
        parts = key.split("/", 1)
        title = period_names.get(parts[0], "气象预报图")
        entity = station_names.get(parts[-1], parts[-1]) if len(parts) > 1 else ""
        labels = tuple(sorted({field_names.get(f, "数据") for f in fields}))
        compact.setdefault((title, reason, labels), []).append(entity)
    for (title, reason, labels), entities_with_gaps in compact.items():
        names = "、".join(n for n in entities_with_gaps if n)
        paragraph(f"{title}：{names + '，' if names else ''}{reason}（{'、'.join(labels)}）。", "note")
    return {"blocks": blocks, "charts": charts, "missing": missing,
            "citations": citations, "disclaimer": KIND_LABELS[m["data_kind"]]}


def validate_view(bundle: Bundle, view: dict) -> None:
    require(view == build_view(bundle), "presentation differs from bound source facts")
