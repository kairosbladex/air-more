"""演示用综合指数（六项污染物分指数求和）与排名。

口径：综合指数 = Σ(污染物浓度 ÷ GB 3095-2012 二级 24h 限值)。
浓度为无人机巡测时段的航次等权均值，**非全天 24h 均值**；
结果仅供演示，非正式达标判定。
"""

from __future__ import annotations

# GB 3095-2012 二级 24h 平均限值（演示口径参考）
COMPOSITE_LIMITS: dict[str, float] = {
    "so2": 150.0,   # μg/m³
    "no2": 80.0,
    "co": 4.0,      # mg/m³
    "o3": 160.0,
    "pm10": 150.0,
    "pm2_5": 75.0,
}

COMPOSITE_FIELDS = ["so2", "no2", "co", "o3", "pm10", "pm2_5"]

# 参考日报表头顺序（首列为区域/站点名）
TABLE_FIELDS = ["so2", "no2", "co", "o3", "pm10", "pm2_5"]

def composite_note(conc_desc: str) -> str:
    """综合指数口径说明（表注/证据用）。conc_desc 为浓度口径描述。"""
    return (
        "综合指数 = Σ(污染物浓度 ÷ GB 3095-2012 二级 24h 限值"
        "（SO2 150、NO2 80、CO 4 mg/m³、O3 160、PM10 150、PM2.5 75 μg/m³）），"
        f"浓度为{conc_desc}，非全天 24h 均值；仅供演示，非正式达标判定"
    )


def index_components(means: dict[str, float | None]) -> dict[str, float | None]:
    """各污染物分指数 = 浓度 ÷ 限值；缺测为 None。"""
    return {f: (round(means[f] / COMPOSITE_LIMITS[f], 4)
                if means.get(f) is not None else None)
            for f in COMPOSITE_FIELDS}


def composite_index(components: dict[str, float | None]) -> float | None:
    """六项分指数求和（2 位小数）；全部缺测返回 None。"""
    vals = [v for v in components.values() if v is not None]
    if not vals:
        return None
    return round(sum(vals), 2)


def assign_ranks(indices: dict[str, float | None]) -> dict[str, int]:
    """按综合指数升序排名（指数低者名次靠前）。

    缺测（None）排在有效值之后，仍占名次（与参考日报一致）。
    """
    valid = sorted((h for h in indices if indices[h] is not None),
                   key=lambda h: (indices[h], h))
    missing = sorted(h for h in indices if indices[h] is None)
    return {h: i + 1 for i, h in enumerate(valid + missing)}


def rank_color(rank: int, n: int) -> str | None:
    """排名后三位黄色、最后一位红色（参考日报规则）。

    排名对象少于 4 个时不着色（"后三"无从谈起）。
    """
    if n < 4 or rank < 1 or rank > n:
        return None
    if rank == n:
        return "red"
    if rank > n - 3:
        return "yellow"
    return None
