"""站点形态配置：无人机巡测 / 固定网格点。

仅做文案与图件参数化（报头声明、口径措辞、图件类型），
不改变确定性流水线架构。默认无人机巡测形态，行为与此前一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from airmore import DISCLAIMER


@dataclass(frozen=True)
class SiteProfile:
    kind: str               # "drone" | "fixed_point"
    disclaimer: str         # 页脚/图注/JSON 的数据声明
    disclaimer_suffix: str  # 附录声明句尾（站点形态相关）
    region: str             # 适用区域描述
    hangar_label: str       # 机库 / 网格点
    sortie_label: str       # 航次 / 自然日
    period_label: str       # 巡测时段 / 观测时段
    weighting_note: str     # 统计口径说明
    source_desc: str        # 数据来源说明（附录）
    issue_note: str         # 期号标注
    section1_title: str     # A 类第一节标题
    table_caption: str      # A 类统计表题名
    conc_desc: str          # 浓度口径（综合指数注）
    chart_map: str          # "trajectory"（航迹图）| "site_map"（单点位置图）
    work_advice_kind: str   # A 类第四节建议口径："drone" | "fixed_point"
    # 持续性判定参数覆盖（小时级数据需要放宽 max_gap 与时长档位）；None 用配置默认
    persistence_override: dict | None = None


DRONE_PROFILE = SiteProfile(
    kind="drone",
    disclaimer=DISCLAIMER,
    disclaimer_suffix="虚构机库与示意航线，不指向真实主体。",
    region="",  # 由 base.hangar_region 按数据生成
    hangar_label="机库",
    sortie_label="航次",
    period_label="巡测时段",
    weighting_note="航次等权平均（mean of sortie means）",
    source_desc="数据来源：数据目录下 {n} 个 CSV（模拟巡测数据，固定随机种子可复算）",
    issue_note="（模拟期号）",
    section1_title="一、巡测区域空气质量情况",
    table_caption="巡测日空气质量情况统计表",
    conc_desc="无人机巡测时段航次等权均值",
    chart_map="trajectory",
    work_advice_kind="drone",
)

FIXED_POINT_PROFILE = SiteProfile(
    kind="fixed_point",
    disclaimer=("数据来源：Open-Meteo（CAMS 空气质量模型 / ERA5 气象），"
                "为网格模型数据，未经地面监测站核实，不用于业务决策"),
    disclaimer_suffix="仅反映单个网格点模型结果，不代表周边区域实测。",
    region="郑州市中原区固定网格点（34.748°N, 113.613°E，Open-Meteo 网格）",
    hangar_label="网格点",
    sortie_label="自然日",
    period_label="观测时段",
    weighting_note="固定网格点小时均值，按自然日等权平均",
    source_desc=("数据来源：Open-Meteo API（CAMS 空气质量模型 / ERA5 气象），"
                 "网格模型数据，未经地面监测站核实；详见数据目录 SOURCES.md"),
    issue_note="（演示期号）",
    section1_title="一、网格点空气质量情况",
    table_caption="观测日空气质量情况统计表",
    conc_desc="固定网格点小时均值按自然日等权",
    chart_map="site_map",
    work_advice_kind="fixed_point",
    # 小时级网格数据：连续判定按小时间隔与小时级时长档位
    persistence_override={"watch_minutes": 60, "review_minutes": 240,
                          "priority_minutes": 720, "peak_factor": 2.0,
                          "max_gap_minutes": 90},
)

PROFILES = {"drone": DRONE_PROFILE, "fixed_point": FIXED_POINT_PROFILE}
