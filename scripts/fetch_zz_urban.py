"""抓取郑州主城 8 区 Open-Meteo 数据（空气质量 CAMS + 气象预报模型）。

数据源（免费、无需 key）：
- 空气质量：https://air-quality-api.open-meteo.com/v1/air-quality （CAMS，μg/m³）
- 气象：https://api.open-meteo.com/v1/forecast （含 weather_code / cloud_cover）

输出到 data/zhengzhou-urban/：
- <区名>.csv：hourly 合并表（past_days 覆盖当月 1 日至昨日，forecast_days=7）
- SOURCES.md：API、坐标、抓取时间

注意：网格模型数据，未经地面监测站核实，不用于业务决策。
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 郑州主城 8 区代表点（区政府/区中心附近）
DISTRICTS: dict[str, tuple[float, float]] = {
    "中原区": (34.748, 113.613),
    "二七区": (34.724, 113.640),
    "金水区": (34.800, 113.681),
    "管城回族区": (34.747, 113.677),
    "惠济区": (34.868, 113.617),
    "郑东新区": (34.771, 113.762),
    "高新区": (34.814, 113.567),
    "经开区": (34.723, 113.739),
}

AQ_FIELDS = ["pm2_5", "pm10", "ozone", "nitrogen_dioxide",
             "sulphur_dioxide", "carbon_monoxide"]
MET_FIELDS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m",
              "wind_direction_10m", "weather_code", "cloud_cover", "precipitation"]

PAST_DAYS = 9       # 覆盖当月 1 日至昨日（抓取日为 9 日）
FORECAST_DAYS = 7   # 预报延伸至 5 日后

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "zhengzhou-urban"

COLUMNS = ["timestamp", "district", "lat", "lon"] + AQ_FIELDS + MET_FIELDS


def fetch(url: str, retries: int = 4) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "air-more-demo/0.1"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 网络瞬断重试
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败（重试 {retries} 次）: {url}\n{last}")


def fetch_district(name: str, lat: float, lon: float) -> list[dict]:
    aq_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly={','.join(AQ_FIELDS)}"
        f"&timezone=Asia%2FShanghai&past_days={PAST_DAYS}"
        f"&forecast_days={FORECAST_DAYS}"
    )
    met_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly={','.join(MET_FIELDS)}"
        "&wind_speed_unit=ms&timezone=Asia%2FShanghai"
        f"&past_days={PAST_DAYS}&forecast_days={FORECAST_DAYS}"
    )
    aq, met = fetch(aq_url), fetch(met_url)
    aq_h, met_h = aq["hourly"], met["hourly"]
    met_idx = {t: i for i, t in enumerate(met_h["time"])}
    rows = []
    for i, t in enumerate(aq_h["time"]):
        j = met_idx.get(t)
        if j is None:
            continue
        row = {"timestamp": f"{t}:00+08:00", "district": name,
               "lat": lat, "lon": lon}
        for f in AQ_FIELDS:
            v = aq_h[f][i]
            row[f] = round(v / 1000.0, 3) if f == "carbon_monoxide" and v is not None else v
        for f in MET_FIELDS:
            row[f] = met_h[f][j]
        rows.append(row)
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for name, (lat, lon) in DISTRICTS.items():
        rows = fetch_district(name, lat, lon)
        path = OUT_DIR / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print(f"{name}: rows={len(rows)} {rows[0]['timestamp']} ~ {rows[-1]['timestamp']}")
        time.sleep(1)  # 友好限速
    (OUT_DIR / "SOURCES.md").write_text(f"""# 数据来源说明（data/zhengzhou-urban）

- 抓取时间（UTC）：{fetched_at}
- 区域：郑州主城 8 区代表点（区政府/区中心附近）
- 空气质量：https://air-quality-api.open-meteo.com/v1/air-quality
  - CAMS 模型，单位 μg/m³；carbon_monoxide 列已 ÷1000 转为 mg/m³
  - past_days={PAST_DAYS}（历史分析）+ forecast_days={FORECAST_DAYS}（预报）
- 气象：https://api.open-meteo.com/v1/forecast
  - temperature_2m(°C)、relative_humidity_2m(%RH)、wind_speed_10m(m/s)、
    wind_direction_10m(°)、weather_code(WMO)、cloud_cover(%)、precipitation(mm)
- 时段：hourly，Asia/Shanghai；历史段 + 预报段合并
- **注意：以上为网格模型数据，未经地面监测站核实，不用于业务决策。**
""", encoding="utf-8")
    print("written", OUT_DIR / "SOURCES.md")


if __name__ == "__main__":
    sys.exit(main())
