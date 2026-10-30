"""HJ 633—2026 daily/real-time index arithmetic, independent of sampling eligibility.

Concentrations passed here MUST already be valid statistics, in reference-state
mass units for gases and ambient-state units for particles. See statistics.py.
Sources and table/section mapping: docs/air-report-statistics.md.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN

from .models import METRICS

VERSION = "HJ633-2026+HJ663-2026+GB3095-2026/v1"
LEVELS = (0, 50, 100, 150, 200, 300, 400, 500)
DAILY = {
    "so2": (0, 50, 150, 475, 800, 1600, 2100, 2620),
    "no2": (0, 40, 80, 180, 280, 565, 750, 940),
    "co": (0, 2, 4, 14, 24, 36, 48, 60),
    "o3": (0, 100, 160, 215, 265, 800),
    "pm10": (0, 50, 120, 250, 350, 420, 500, 600),
    "pm2_5": (0, 35, 60, 115, 150, 250, 350, 500),
}
HOURLY = DAILY | {
    "so2": (0, 150, 500, 650, 800), "no2": (0, 100, 200, 700, 1200, 2340, 3090, 3840),
    "co": (0, 5, 10, 35, 60, 90, 120, 150), "o3": (0, 160, 200, 300, 400, 800, 1000, 1200),
}
CATEGORIES = ((50, "优", "00E400"), (100, "良", "FFFF00"), (150, "轻度污染", "FF7E00"),
              (200, "中度污染", "FF0000"), (300, "重度污染", "99004C"), (500, "严重污染", "7E0023"))


def rounded_concentration(metric: str, value) -> Decimal:
    v = Decimal(str(value))
    if not v.is_finite() or v < 0:
        raise ValueError("浓度必须是有限非负值")
    return v.quantize(Decimal("0.1") if metric == "co" else Decimal("1"), rounding=ROUND_HALF_EVEN)


def iaqi(metric: str, value, period="day") -> int:
    if period not in {"day", "hour"} or metric not in METRICS:
        raise ValueError("未知指标或时段")
    v = rounded_concentration(metric, value)
    bounds = (DAILY if period == "day" else HOURLY)[metric]
    if v >= bounds[-1]:
        return LEVELS[len(bounds) - 1]  # Table 3 footnotes: SO2 hour 200, O3 8h 300.
    for idx in range(1, len(bounds)):
        if v <= bounds[idx]:
            low, high = Decimal(bounds[idx - 1]), Decimal(bounds[idx])
            index = (v - low) * (LEVELS[idx] - LEVELS[idx - 1]) / (high - low) + LEVELS[idx - 1]
            return int(index.to_integral_value(rounding=ROUND_CEILING))
    raise AssertionError("unreachable")


def calculate(concentrations: dict, period="day") -> dict:
    indices = {m: iaqi(m, concentrations[m], period) for m in METRICS if concentrations.get(m) is not None}
    missing = [m for m in METRICS if m not in indices]
    maximum = max(indices.values(), default=0)
    # HJ633 §5.3.1; the §5.3.2 three-year exception needs evidence, never inferred.
    if not indices or (missing and maximum <= 100):
        return {"aqi": None, "iaqi": indices, "category": None, "level": None,
                "color": None, "primary_pollutants": [], "missing": missing, "partial": bool(missing)}
    # §4.1 defines AQI range 1–500 even when all rounded IAQI are zero.
    value = max(1, maximum)
    level, category, color = next((i + 1, c, color) for i, (limit, c, color) in enumerate(CATEGORIES) if value <= limit)
    return {"aqi": value, "iaqi": indices, "category": category, "level": level, "color": color,
            "primary_pollutants": [m for m, v in indices.items() if v == maximum] if value > 50 else [],
            "missing": missing, "partial": bool(missing)}


def ozone_daily(hourly: dict[int, float], limit=160) -> tuple[float | None, int]:
    """Hour keys 0..23 are start hours (00–01 is the 01:00 observation).

    HJ663 Annex A: 17 windows ending 08:00..24:00. Each requires 6 valid
    hours; >=14 windows, or a maximum EXCEEDING the applicable O3 limit.
    """
    windows = []
    for end in range(8, 25):
        values = [Decimal(str(hourly[h])) for h in range(end - 8, end) if h in hourly]
        if len(values) >= 6:
            windows.append(sum(values) / len(values))
    if not windows:
        return None, 0
    value = max(windows)
    return (float(value) if len(windows) >= 14 or value > limit else None), len(windows)
