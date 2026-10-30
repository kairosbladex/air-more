"""生成可复算的模拟巡测数据（固定随机种子）。

场景：2026-03-15 一个完整巡测日，4 个虚构机库（HANGAR-A..D），
共 8 个航次。数据中埋入：
- S-A-2 航次 10:15–10:45 一段 PM2.5 连续偏高事件（PM10 同步抬升）；
- 少量缺测（S-B-1 的 pm2_5、S-A-3 的 humidity）；
- S-A-1 中 3 行完全重复记录（供质控演示）；
- 各机库基线水平不同（A 最高、D 最低），供排名演示；
- 其余为正常背景段。
S-A-2 采用 10 秒高频采样，其余航次 60 秒，用于演示"航次等权平均"。

输出列（单位）：
timestamp(ISO8601+08:00), sortie_id, hangar_id, lat, lon, altitude_m,
pm1_0/pm2_5/pm10/so2/no2/o3(μg/m³), co(mg/m³), ch4(ppm), ox(含义未明),
temperature(°C), humidity(%RH), pressure(hPa), wind_speed(m/s), wind_direction(°)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
DAY = "2026-03-15"
TZ = "+08:00"

# 虚构机库中心坐标（不指向真实主体）
HANGARS = {
    "HANGAR-A": (31.2300, 121.4700),
    "HANGAR-B": (31.2650, 121.5200),
    "HANGAR-C": (31.2450, 121.4950),
    "HANGAR-D": (31.2120, 121.5000),
}

# 各机库基线偏移（μg/m³）：使综合指数排名有区分度，A 最高、D 最低
PROFILES = {
    "HANGAR-A": {"pm2_5": 6.0, "no2": 6.0, "o3": 0.0},
    "HANGAR-B": {"pm2_5": 1.0, "no2": 10.0, "o3": 6.0},
    "HANGAR-C": {"pm2_5": -4.0, "no2": 2.0, "o3": 10.0},
    "HANGAR-D": {"pm2_5": -10.0, "no2": -6.0, "o3": -4.0},
}

# (sortie_id, hangar, start, end, 采样间隔秒)
SORTIES = [
    ("S-A-1", "HANGAR-A", "08:00", "08:45", 60),
    ("S-A-2", "HANGAR-A", "10:00", "10:50", 10),   # 高频航次，内含 PM2.5 事件
    ("S-A-3", "HANGAR-A", "14:00", "14:40", 60),
    ("S-B-1", "HANGAR-B", "09:00", "09:40", 60),
    ("S-B-2", "HANGAR-B", "15:00", "15:45", 60),
    ("S-C-1", "HANGAR-C", "08:30", "09:15", 60),
    ("S-C-2", "HANGAR-C", "13:00", "13:45", 60),
    ("S-D-1", "HANGAR-D", "10:30", "11:10", 60),
]

# PM2.5 事件窗口（S-A-2 内）
EVENT_START, EVENT_END = "10:15", "10:45"


def _track(center: tuple[float, float], n: int, rng: np.random.Generator,
           phase: float) -> tuple[np.ndarray, np.ndarray]:
    """绕机库中心的椭圆示意航线。"""
    t = np.linspace(0, 2 * np.pi, n) + phase
    lat = center[0] + 0.009 * np.sin(t) + rng.normal(0, 0.0003, n)
    lon = center[1] + 0.013 * np.cos(t) + rng.normal(0, 0.0003, n)
    return lat, lon


def _smooth(rng: np.random.Generator, n: int, scale: float) -> np.ndarray:
    """平滑随机噪声（滑动平均），模拟连续变化。"""
    x = rng.normal(0, scale, n)
    k = np.ones(5) / 5
    return np.convolve(x, k, mode="same")


def build_sortie(sid: str, hid: str, start: str, end: str, step_s: int,
                 rng: np.random.Generator) -> pd.DataFrame:
    idx = pd.date_range(f"{DAY} {start}{TZ}", f"{DAY} {end}{TZ}",
                        freq=f"{step_s}s")
    n = len(idx)
    hour = idx.hour + idx.minute / 60
    lat, lon = _track(HANGARS[hid], n, rng, phase=hash(sid) % 6)

    # 气象：温度日变化 + 平滑噪声
    temperature = 12.5 + 5.5 * np.sin((hour - 8) / 24 * 2 * np.pi) + _smooth(rng, n, 0.3)
    humidity = 68 - 18 * np.sin((hour - 8) / 24 * 2 * np.pi) + _smooth(rng, n, 1.5)
    pressure = 1015.2 + _smooth(rng, n, 0.4)
    wind_speed = np.clip(2.6 + _smooth(rng, n, 0.8), 0.3, None)
    wind_direction = (225 + _smooth(rng, n, 12)) % 360
    altitude = 80 + 25 * np.sin(np.linspace(0, 3 * np.pi, n)) + _smooth(rng, n, 2)

    # 污染物背景（含机库基线偏移）
    prof = PROFILES[hid]
    pm2_5 = np.clip(30 + prof["pm2_5"] + _smooth(rng, n, 5), 5, None)
    pm10 = pm2_5 * 1.35 + np.clip(_smooth(rng, n, 4), 0, None) + 6
    pm1_0 = pm2_5 * 0.55 + np.clip(_smooth(rng, n, 2), 0, None)
    so2 = np.clip(8 + _smooth(rng, n, 1.5), 1, None)
    no2 = np.clip(24 + prof["no2"] + _smooth(rng, n, 4), 3, None)
    o3 = np.clip(62 + prof["o3"] + 20 * np.sin((hour - 9) / 24 * 2 * np.pi)
                 + _smooth(rng, n, 5), 10, None)
    co = np.clip(0.62 + _smooth(rng, n, 0.08), 0.1, None)
    ch4 = np.clip(1.89 + _smooth(rng, n, 0.02), 1.7, None)
    ox = np.clip(0.5 + _smooth(rng, n, 0.2), 0, None)  # 含义未明，不参与计算

    # 埋入 PM2.5 连续偏高事件（PM10 同步抬升）
    if sid == "S-A-2":
        t0 = pd.Timestamp(f"{DAY} {EVENT_START}{TZ}")
        t1 = pd.Timestamp(f"{DAY} {EVENT_END}{TZ}")
        in_event = (idx >= t0) & (idx <= t1)
        m = in_event.sum()
        # 钟形抬升，峰值约 +115
        bump = 115 * np.sin(np.linspace(0, np.pi, m)) ** 1.2
        bump += rng.normal(0, 4, m)
        pm2_5[in_event] += np.clip(bump, 0, None)
        pm10[in_event] = pm2_5[in_event] * 1.18 + rng.normal(0, 3, m)
        pm1_0[in_event] = pm2_5[in_event] * 0.6

    df = pd.DataFrame({
        "timestamp": idx.strftime("%Y-%m-%dT%H:%M:%S") + TZ,
        "sortie_id": sid, "hangar_id": hid,
        "lat": np.round(lat, 5), "lon": np.round(lon, 5),
        "altitude_m": np.round(altitude, 1),
        "pm1_0": np.round(pm1_0, 1), "pm2_5": np.round(pm2_5, 1),
        "pm10": np.round(pm10, 1), "so2": np.round(so2, 1),
        "no2": np.round(no2, 1), "o3": np.round(o3, 1),
        "co": np.round(co, 2), "ch4": np.round(ch4, 2),
        "ox": np.round(ox, 2),
        "temperature": np.round(temperature, 1),
        "humidity": np.round(humidity, 1),
        "pressure": np.round(pressure, 1),
        "wind_speed": np.round(wind_speed, 1),
        "wind_direction": np.round(wind_direction, 0),
    })
    return df


def inject_defects(df: pd.DataFrame) -> pd.DataFrame:
    """埋入缺测与重复行。"""
    # 缺测：S-B-1 连续 3 行 pm2_5；S-A-3 2 行 humidity
    b1 = df.index[df["sortie_id"] == "S-B-1"]
    df.loc[b1[10:13], "pm2_5"] = np.nan
    a3 = df.index[df["sortie_id"] == "S-A-3"]
    df.loc[a3[5:6], "humidity"] = np.nan
    # 重复：S-A-1 中 3 行原样复制
    a1 = df[df["sortie_id"] == "S-A-1"].iloc[[4, 12, 20]]
    df = pd.concat([df, a1], ignore_index=True)
    return df


def main() -> None:
    rng = np.random.default_rng(SEED)
    out_dir = Path(__file__).resolve().parent.parent / "data" / "sample"
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.concat(
        [build_sortie(sid, hid, s, e, step, rng)
         for sid, hid, s, e, step in SORTIES],
        ignore_index=True,
    )
    df = inject_defects(df)
    df = df.sort_values("timestamp").reset_index(drop=True)
    for hid in HANGARS:
        sub = df[df["hangar_id"] == hid].reset_index(drop=True)
        path = out_dir / f"{hid}_{DAY}.csv"
        sub.to_csv(path, index=False)
        print(f"written {path} rows={len(sub)}")


if __name__ == "__main__":
    main()
