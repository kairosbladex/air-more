"""出图：航迹散点着色图、指标时间曲线、分机库对比、贡献率饼图、单点位置图。

每张图都带标题、时间范围、单位、图例和数据来源声明（由调用方按
站点形态传入，无人机模拟数据 / Open-Meteo 网格模型数据）。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from airmore import DISCLAIMER
from airmore.domain.indicators import POLLUTANTS

# macOS 中文字体回退
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB",
                                   "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _footer(fig: plt.Figure, source_note: str) -> None:
    fig.text(0.01, 0.01, source_note, fontsize=7, color="gray")


def source_note(kind: str, disclaimer: str) -> str:
    if kind == "fixed_point":
        return f"数据来源：Open-Meteo 网格模型数据（未经地面站核实）｜{disclaimer}"
    return f"数据来源：无人机巡测模拟数据（虚构机库）｜{disclaimer}"


def trajectory_chart(df: pd.DataFrame, color_field: str, out: Path,
                     date_label: str,
                     note: str | None = None) -> Path:
    """航迹散点着色图（兼作首页位置图）。"""
    ind = POLLUTANTS[color_field]
    note = note or source_note("drone", DISCLAIMER)
    fig, ax = plt.subplots(figsize=(6.2, 4.6), dpi=150)
    markers = {"HANGAR-A": "o", "HANGAR-B": "s"}
    for hid, g in df.groupby("hangar_id"):
        sc = ax.scatter(g["lon"], g["lat"], c=g[color_field], cmap="YlOrRd",
                        s=8, marker=markers.get(hid, "o"), label=hid,
                        vmin=0, alpha=0.85)
    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label(f"{ind.label}（{ind.unit}）")
    ax.set_xlabel("经度（°）")
    ax.set_ylabel("纬度（°）")
    ax.set_title(f"巡测航迹与{ind.label}分布（{date_label}）\n虚构机库示意航线，仅显示实际观测点")
    ax.legend(title="机库", loc="upper right")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    _footer(fig, note)
    fig.savefig(out)
    plt.close(fig)
    return out


def site_map_chart(df: pd.DataFrame, out: Path, date_label: str,
                   note: str | None = None) -> Path:
    """固定网格点位置图（单点，固定站点形态使用）。"""
    from matplotlib.ticker import MaxNLocator

    note = note or source_note("fixed_point", DISCLAIMER)
    lat = float(df["lat"].iloc[0])
    lon = float(df["lon"].iloc[0])
    hid = str(df["hangar_id"].iloc[0])
    fig, ax = plt.subplots(figsize=(4.6, 3.4), dpi=150)
    ax.scatter([lon], [lat], marker="o", s=90, color="#B03030", zorder=3,
               label=hid)
    ax.annotate(f"{hid}\n({lat:.3f}°N, {lon:.3f}°E)", (lon, lat),
                textcoords="offset points", xytext=(12, -4), fontsize=8)
    pad = 0.02
    ax.set_xlim(lon - pad, lon + pad)
    ax.set_ylim(lat - pad, lat + pad)
    ax.xaxis.set_major_locator(MaxNLocator(4))
    ax.yaxis.set_major_locator(MaxNLocator(4))
    ax.set_xlabel("经度（°）")
    ax.set_ylabel("纬度（°）")
    ax.set_title(f"观测网格点位置图（{date_label}）")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    _footer(fig, note)
    fig.savefig(out)
    plt.close(fig)
    return out


def timeseries_chart(df: pd.DataFrame, field: str, out: Path,
                     threshold: float | None = None,
                     date_label: str = "",
                     note: str | None = None) -> Path:
    """指标时间曲线，按航次/自然日分段着色，叠加筛查阈值线。"""
    ind = POLLUTANTS[field]
    note = note or source_note("drone", DISCLAIMER)
    fig, ax = plt.subplots(figsize=(6.8, 3.2), dpi=150)
    for sid, g in df.groupby("sortie_id"):
        g = g.sort_values("timestamp")
        ax.plot(g["timestamp"], g[field], linewidth=1.0, label=sid)
    if threshold is not None:
        ax.axhline(threshold, color="red", linestyle="--", linewidth=1.0,
                   label=f"演示筛查阈值 {threshold:g} {ind.unit}")
    ax.set_xlabel(f"时间（{date_label}，UTC+8）")
    ax.set_ylabel(f"{ind.label}（{ind.unit}）")
    ax.set_title(f"{ind.label}观测曲线（{date_label}）")
    ax.legend(fontsize=6, ncol=3, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    _footer(fig, note)
    fig.savefig(out)
    plt.close(fig)
    return out


def hangar_compare_chart(hangar_stats: dict[str, dict], field: str,
                         out: Path, threshold: float | None = None,
                         date_label: str = "",
                         note: str | None = None) -> Path:
    """分机库对比柱状图（航次等权均值与最大值）。"""
    ind = POLLUTANTS[field]
    note = note or source_note("drone", DISCLAIMER)
    hangars = list(hangar_stats.keys())
    means = [hangar_stats[h]["mean"] or 0 for h in hangars]
    maxs = [hangar_stats[h]["max"] or 0 for h in hangars]
    x = range(len(hangars))
    fig, ax = plt.subplots(figsize=(4.6, 3.2), dpi=150)
    w = 0.35
    ax.bar([i - w / 2 for i in x], means, width=w, label="航次等权均值")
    ax.bar([i + w / 2 for i in x], maxs, width=w, label="航次最大值")
    if threshold is not None:
        ax.axhline(threshold, color="red", linestyle="--", linewidth=1.0,
                   label=f"演示筛查阈值 {threshold:g} {ind.unit}")
    ax.set_xticks(list(x), hangars)
    ax.set_ylabel(f"{ind.label}（{ind.unit}）")
    ax.set_title(f"分机库{ind.label}对比（{date_label}）")
    ax.legend(fontsize=7)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    _footer(fig, note)
    fig.savefig(out)
    plt.close(fig)
    return out


_PIE_COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2", "#9D755D"]


def contribution_pie_chart(components_by_hangar: dict[str, dict[str, float | None]],
                           out: Path, date_label: str,
                           note: str | None = None,
                           day_label: str = "巡测日") -> Path:
    """六项污染物贡献率占比图（每站点一张饼图，2 列排布）。

    占比 = 各污染物分指数（浓度÷GB 3095-2012 二级 24h 限值）归一化，仅供演示。
    """
    from airmore.domain.composite import COMPOSITE_FIELDS

    note = note or source_note("drone", DISCLAIMER)
    hangars = sorted(components_by_hangar)
    n = len(hangars)
    ncols = 2
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.6, 2.9 * nrows), dpi=150)
    axes = list(axes.flat) if hasattr(axes, "flat") else [axes]
    for ax, hid in zip(axes, hangars):
        comps = components_by_hangar[hid]
        vals = [comps.get(f) or 0 for f in COMPOSITE_FIELDS]
        total = sum(vals) or 1.0
        labels = [POLLUTANTS[f].label for f in COMPOSITE_FIELDS]
        ax.pie([v / total for v in vals], labels=labels, autopct="%.0f%%",
               colors=_PIE_COLORS, textprops={"fontsize": 7},
               startangle=90, counterclock=False)
        ax.set_title(hid, fontsize=9)
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle(
        f"{day_label}六项污染物贡献率占比图（{date_label}）\n"
        "占比按演示口径分指数（浓度÷GB 3095-2012 二级 24h 限值）归一化，仅供演示",
        fontsize=10)
    fig.tight_layout(rect=(0, 0.04, 1, 0.93))
    _footer(fig, note)
    fig.savefig(out)
    plt.close(fig)
    return out
