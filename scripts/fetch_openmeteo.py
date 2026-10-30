"""抓取 Open-Meteo 真实数据（郑州市中原区固定网格点），合并为项目 CSV schema。

数据源（免费、无需 key）：
- 空气质量：CAMS 模型（air-quality-api.open-meteo.com），单位 μg/m³
- 气象：ERA5/预报模型（api.open-meteo.com），wind_speed_unit=ms

输出到 data/zhengzhou/：
- ZZ-Zhongyuan_2026-09-01_2026-09-06.csv（hourly，timestamp 带 +08:00）
- SOURCES.md（API、坐标、参数、抓取时间）

映射说明：
- sortie_id 按自然日划分（D-0901..D-0906），hangar_id=ZZ-Zhongyuan；
- co 列 = carbon_monoxide μg/m³ ÷ 1000（mg/m³）；
- pm1_0 / ch4 / ox 无数据，留空（按缺测处理）；
- altitude_m 取 API 返回 elevation。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAT, LON = 34.748, 113.613
HANGAR_ID = "ZZ-Zhongyuan"
DATE_START, DATE_END = "2026-09-01", "2026-09-06"  # 最近完整日截止（今天 2026-09-07）

AQ_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=pm2_5,pm10,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide"
    "&timezone=Asia%2FShanghai&past_days=7"
)
MET_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=temperature_2m,relative_humidity_2m,surface_pressure,"
    "wind_speed_10m,wind_direction_10m"
    "&wind_speed_unit=ms&timezone=Asia%2FShanghai&past_days=7"
)

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "zhengzhou"

COLUMNS = [
    "timestamp", "sortie_id", "hangar_id", "lat", "lon", "altitude_m",
    "pm1_0", "pm2_5", "pm10", "so2", "no2", "o3", "co", "ch4", "ox",
    "temperature", "humidity", "pressure", "wind_speed", "wind_direction",
]


def fetch(url: str, retries: int = 4) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "air-more-demo/0.1"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 网络瞬断重试
            last = exc
            import time
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败（重试 {retries} 次）: {url}\n{last}")


def main() -> None:
    aq = fetch(AQ_URL)
    met = fetch(MET_URL)
    elevation = met.get("elevation") or aq.get("elevation") or 114

    aq_h, met_h = aq["hourly"], met["hourly"]
    times = [t for t in aq_h["time"] if DATE_START <= t[:10] <= DATE_END]
    met_by_time = {t: i for i, t in enumerate(met_h["time"])}

    rows = []
    for t in times:
        i = aq_h["time"].index(t)
        j = met_by_time.get(t)
        if j is None:
            continue
        co_ugm3 = aq_h["carbon_monoxide"][i]
        rows.append({
            "timestamp": f"{t}:00+08:00",
            "sortie_id": "D-" + t[5:7] + t[8:10],
            "hangar_id": HANGAR_ID,
            "lat": LAT, "lon": LON, "altitude_m": elevation,
            "pm1_0": "",
            "pm2_5": aq_h["pm2_5"][i],
            "pm10": aq_h["pm10"][i],
            "so2": aq_h["sulphur_dioxide"][i],
            "no2": aq_h["nitrogen_dioxide"][i],
            "o3": aq_h["ozone"][i],
            "co": round(co_ugm3 / 1000.0, 3) if co_ugm3 is not None else "",
            "ch4": "", "ox": "",
            "temperature": met_h["temperature_2m"][j],
            "humidity": met_h["relative_humidity_2m"][j],
            "pressure": met_h["surface_pressure"][j],
            "wind_speed": met_h["wind_speed_10m"][j],
            "wind_direction": met_h["wind_direction_10m"][j],
        })

    import csv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{HANGAR_ID}_{DATE_START}_{DATE_END}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT_DIR / "SOURCES.md").write_text(f"""# 数据来源说明（data/zhengzhou）

- 抓取时间（UTC）：{fetched_at}
- 网格点：郑州市中原区 lat={LAT}, lon={LON}，elevation={elevation} m（API 返回）
- 空气质量：{AQ_URL}
  - CAMS 空气质量模型，单位 μg/m³；co 列已 ÷1000 转为 mg/m³
- 气象：{MET_URL}
  - ERA5/预报模型；temperature_2m(°C)、relative_humidity_2m(%RH)、
    surface_pressure(hPa)、wind_speed_10m(m/s)、wind_direction_10m(°)
- 时段：{DATE_START} 至 {DATE_END}（hourly，Asia/Shanghai；已剔除不完整的 09-07）
- 时段内行数：{len(rows)}
- pm1_0 / ch4 / ox：数据源无此字段，留空按缺测处理
- **注意：以上为网格模型数据，未经地面监测站核实，不用于业务决策。**
""", encoding="utf-8")

    # 概况输出
    print(f"written {path} rows={len(rows)} elevation={elevation}")
    print(f"time range: {rows[0]['timestamp']} ~ {rows[-1]['timestamp']}")
    for f in ["pm2_5", "pm10", "so2", "no2", "o3", "co",
              "temperature", "humidity", "pressure", "wind_speed"]:
        vals = [r[f] for r in rows if r[f] not in ("", None)]
        print(f"  {f}: min={min(vals)} max={max(vals)}")


if __name__ == "__main__":
    sys.exit(main())
