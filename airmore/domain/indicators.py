"""指标与单位定义（按真实标准设计）。

污染物单位：
- pm1_0 / pm2_5 / pm10 / so2 / no2 / o3：μg/m³
- co：mg/m³
- ch4：ppm
- ox：含义不明（方案文档第 1 节），不参与任何计算，仅在质控报告中标注。

气象单位：温度 °C、相对湿度 %RH、气压 hPa、风速 m/s、风向 °、能见度 km、降水 mm。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Indicator:
    name: str          # CSV 字段名
    label: str         # 中文显示名
    unit: str          # 展示单位
    molecular_weight: float | None = None  # g/mol，仅气体需要
    participates: bool = True              # 是否参与计算
    note: str = ""


# 分子量（g/mol）：SO2 64.06、NO2 46.01、O3 48.00、CO 28.01、CH4 16.04
POLLUTANTS: dict[str, Indicator] = {
    "pm1_0": Indicator("pm1_0", "PM1.0", "μg/m³"),
    "pm2_5": Indicator("pm2_5", "PM2.5", "μg/m³"),
    "pm10": Indicator("pm10", "PM10", "μg/m³"),
    "so2": Indicator("so2", "SO2", "μg/m³", molecular_weight=64.06),
    "no2": Indicator("no2", "NO2", "μg/m³", molecular_weight=46.01),
    "o3": Indicator("o3", "O3", "μg/m³", molecular_weight=48.00),
    "co": Indicator("co", "CO", "mg/m³", molecular_weight=28.01),
    "ch4": Indicator("ch4", "CH4", "ppm", molecular_weight=16.04),
    # ox 含义未明：不参与任何计算，只在质控报告中标注
    "ox": Indicator("ox", "OX(含义未明)", "未知", participates=False,
                    note="字段含义不明，未参与计算"),
}

MET_FIELDS: dict[str, Indicator] = {
    "temperature": Indicator("temperature", "温度", "°C"),
    "humidity": Indicator("humidity", "相对湿度", "%RH"),
    "pressure": Indicator("pressure", "气压", "hPa"),
    "wind_speed": Indicator("wind_speed", "风速", "m/s"),
    "wind_direction": Indicator("wind_direction", "风向", "°"),
    "visibility": Indicator("visibility", "能见度", "km"),
    "precipitation": Indicator("precipitation", "降水", "mm"),
}

# 参与筛查与统计的污染物字段（ox 被排除）
COMPUTED_POLLUTANTS: list[str] = [
    name for name, ind in POLLUTANTS.items() if ind.participates
]

# 物理合理范围（质控范围检查用，宽区间，仅排除明显错误）
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "pm1_0": (0.0, 2000.0),
    "pm2_5": (0.0, 2000.0),
    "pm10": (0.0, 3000.0),
    "so2": (0.0, 5000.0),
    "no2": (0.0, 3000.0),
    "o3": (0.0, 3000.0),
    "co": (0.0, 200.0),      # mg/m³
    "ch4": (0.5, 100.0),     # ppm，背景约 1.8–1.9
    "temperature": (-40.0, 60.0),
    "humidity": (0.0, 100.0),
    "pressure": (800.0, 1100.0),
    "wind_speed": (0.0, 60.0),
    "wind_direction": (0.0, 360.0),
    "altitude_m": (-100.0, 1000.0),
    "lat": (-90.0, 90.0),
    "lon": (-180.0, 180.0),
}
