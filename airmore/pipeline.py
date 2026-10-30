"""确定性流水线：数据接入 → 质控 → 统计 → 规则判定 → 结构化证据包
→ 出图 → 受限写作 → 数值校验 → DOCX/PDF 渲染。

LLM 只是受限写作器；所有数值由程序从证据包回填并校验。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from airmore.charts import plots
from airmore.domain.composite import (COMPOSITE_FIELDS, assign_ranks,
                                      composite_index, index_components)
from airmore.domain.profile import DRONE_PROFILE, SiteProfile
from airmore.ingest.csv_loader import load_csv_dir
from airmore.qc.checks import run_qc
from airmore.reports import report_a, report_b, report_g
from airmore.reports.base import LOCAL_TZ, ReportContext
from airmore.rules.screening import ThresholdConfig, screen_events
from airmore.stats.aggregate import (daily_summary, hangar_field_means,
                                     hangar_summary, sortie_stats)

REPORT_BUILDERS = {"A": report_a.build, "B": report_b.build, "G": report_g.build}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLDS = PROJECT_ROOT / "config" / "thresholds_demo.json"


def build_charts(df: pd.DataFrame, ctx_cfg: ThresholdConfig,
                 out_dir: Path, date_label: str,
                 hangar_index: dict, profile: SiteProfile) -> dict[str, Path]:
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    plot_df = df.copy()
    plot_df["timestamp"] = plot_df["timestamp"].dt.tz_convert(LOCAL_TZ)
    th = float(ctx_cfg.pollutants["pm2_5"]["threshold"])
    note = plots.source_note(profile.kind, profile.disclaimer)
    charts = {
        "ts_pm2_5": plots.timeseries_chart(
            plot_df, "pm2_5", chart_dir / "timeseries_pm2_5.png",
            threshold=th, date_label=date_label, note=note),
        "pies": plots.contribution_pie_chart(
            {h: v["components"] for h, v in hangar_index.items()},
            chart_dir / "contribution_pies.png", date_label, note=note,
            day_label="巡测日" if profile.kind == "drone" else "观测日"),
    }
    if profile.chart_map == "site_map":
        charts["site_map"] = plots.site_map_chart(
            plot_df, chart_dir / "site_map.png", date_label, note=note)
    else:
        charts["trajectory"] = plots.trajectory_chart(
            plot_df, "pm2_5", chart_dir / "trajectory_pm2_5.png", date_label,
            note=note)
    s_stats = sortie_stats(df, ["pm2_5"])
    charts["hangar_pm2_5"] = plots.hangar_compare_chart(
        hangar_summary(s_stats, "pm2_5"), "pm2_5",
        chart_dir / "hangar_pm2_5.png", threshold=th, date_label=date_label,
        note=note)
    return charts


def run(data_dir: str | Path, out_dir: str | Path, reports: list[str],
        thresholds_path: str | Path | None = None,
        use_ollama: bool = False,
        profile: SiteProfile = DRONE_PROFILE,
        report_date: str | None = None) -> dict:
    out_dir = Path(out_dir)
    cfg = ThresholdConfig.load(thresholds_path or DEFAULT_THRESHOLDS)
    if profile.persistence_override:
        cfg.persistence.update(profile.persistence_override)

    # 1. 数据接入
    ingest = load_csv_dir(data_dir)
    # 2. 质控
    qc = run_qc(ingest.df, sortie_label=profile.sortie_label)
    df = qc.df
    if df.empty:
        raise ValueError("质控后无有效数据，无法生成报告（不产出任何结论）")

    # 3. 统计
    sortie = sortie_stats(df)
    daily = daily_summary(sortie)

    # 3b. 演示综合指数（六项分指数求和，站点排名）
    hangar_means = hangar_field_means(sortie, COMPOSITE_FIELDS)
    components = {h: index_components(m) for h, m in hangar_means.items()}
    indices = {h: composite_index(c) for h, c in components.items()}
    ranks = assign_ranks(indices)
    hangar_index = {
        h: {"means": hangar_means[h], "components": components[h],
            "index": indices[h], "rank": ranks[h]}
        for h in hangar_means
    }

    # 4. 规则判定
    events = screen_events(df, cfg)

    # 时间范围（展示用 UTC+8）；报告日期默认取数据起始日，可指定最近完整日
    start, end = df["timestamp"].min(), df["timestamp"].max()
    date_label = report_date or start.tz_convert(LOCAL_TZ).strftime("%Y-%m-%d")

    # 5. 出图
    charts = build_charts(df, cfg, out_dir, date_label, hangar_index, profile)

    ctx = ReportContext(
        df=df, qc=qc, sortie=sortie, daily=daily, events=events, cfg=cfg,
        charts=charts, out_dir=out_dir, use_ollama=use_ollama,
        date_label=date_label, period_start=start, period_end=end,
        hangar_index=hangar_index, profile=profile,
    )

    # 6–9. 证据包 → 受限写作 → 数值校验 → 渲染（各报告独立构建，共用同一事件）
    artifacts: dict[str, dict] = {}
    for r in reports:
        key = r.strip().upper()
        if key not in REPORT_BUILDERS:
            raise ValueError(f"未知报告类型: {r}（当前实现 A/B/G；C–G 预留扩展）")
        artifacts[key] = REPORT_BUILDERS[key](ctx)

    return {
        "artifacts": artifacts,
        "ingest_issues": ingest.issues,
        "qc_records": qc.to_rows(),
        "events": len(events),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "charts": {k: str(v) for k, v in charts.items()},
    }
