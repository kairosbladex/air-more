"""统计聚合：按航次与时间窗聚合。

为避免高频航次支配全日平均：先算各航次统计量，全日汇总时对每个航次
等权平均（航次间等权），而不是按样本数加权。采样覆盖在结果中显式记录。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from airmore.domain.indicators import COMPUTED_POLLUTANTS, POLLUTANTS

MET_AGG_FIELDS = ["temperature", "humidity", "pressure", "wind_speed"]


def _round(v: float | None, nd: int = 1) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), nd)


def sortie_stats(df: pd.DataFrame, fields: list[str] | None = None) -> dict[str, dict]:
    """按航次聚合，返回 {sortie_id: {field: {mean,max,min,count,...}}}。"""
    fields = fields or (COMPUTED_POLLUTANTS + MET_AGG_FIELDS)
    out: dict[str, dict] = {}
    for sid, g in df.groupby("sortie_id"):
        entry: dict = {
            "hangar_id": g["hangar_id"].iloc[0],
            "start": g["timestamp"].min().isoformat(),
            "end": g["timestamp"].max().isoformat(),
            "samples": int(len(g)),
            "lat_center": _round(g["lat"].mean(), 5),
            "lon_center": _round(g["lon"].mean(), 5),
            "altitude_mean_m": _round(g["altitude_m"].mean()),
            "fields": {},
        }
        for f in fields:
            s = g[f].dropna()
            if len(s) == 0:
                entry["fields"][f] = {"mean": None, "max": None, "min": None, "count": 0}
            else:
                imax = s.idxmax()
                entry["fields"][f] = {
                    "mean": _round(s.mean()),
                    "max": _round(s.max()),
                    "min": _round(s.min()),
                    "count": int(len(s)),
                    "max_time": df.loc[imax, "timestamp"].isoformat(),
                    "max_lat": _round(df.loc[imax, "lat"], 5),
                    "max_lon": _round(df.loc[imax, "lon"], 5),
                }
        out[str(sid)] = entry
    return out


def daily_summary(sortie: dict[str, dict], fields: list[str] | None = None) -> dict:
    """全日汇总：各航次均值的等权平均（避免高频航次支配）。

    max 取各航次 max 的最大值（极值不受平均方式影响），并保留其出处航次。
    """
    fields = fields or (COMPUTED_POLLUTANTS + MET_AGG_FIELDS)
    res: dict = {"weighting": "航次等权平均（mean of sortie means）", "fields": {}}
    for f in fields:
        means = [v["fields"][f]["mean"] for v in sortie.values()
                 if v["fields"][f]["mean"] is not None]
        maxima = [(v["fields"][f]["max"], sid, v["fields"][f])
                  for sid, v in sortie.items() if v["fields"][f]["max"] is not None]
        entry = {
            "unit": POLLUTANTS[f].unit if f in POLLUTANTS else "",
            "mean": _round(float(np.mean(means))) if means else None,
            "sorties_contributing": len(means),
        }
        if maxima:
            peak_val, peak_sid, peak_f = max(maxima, key=lambda x: x[0])
            entry.update({
                "max": peak_val, "max_sortie": peak_sid,
                "max_time": peak_f["max_time"],
                "max_lat": peak_f["max_lat"], "max_lon": peak_f["max_lon"],
            })
        res["fields"][f] = entry
    return res


def hangar_summary(sortie: dict[str, dict], field: str) -> dict[str, dict]:
    """分机库对比：对机库内航次等权平均。"""
    by_hangar: dict[str, list[dict]] = {}
    for v in sortie.values():
        by_hangar.setdefault(v["hangar_id"], []).append(v["fields"][field])
    out = {}
    for hid, flist in by_hangar.items():
        means = [x["mean"] for x in flist if x["mean"] is not None]
        maxs = [x["max"] for x in flist if x["max"] is not None]
        out[hid] = {
            "mean": _round(float(np.mean(means))) if means else None,
            "max": max(maxs) if maxs else None,
            "sorties": len(flist),
        }
    return out


def hangar_field_means(sortie: dict[str, dict],
                       fields: list[str]) -> dict[str, dict[str, float | None]]:
    """各机库多字段均值（机库内航次等权平均），缺测字段为 None。"""
    by_hangar: dict[str, list[dict]] = {}
    for v in sortie.values():
        by_hangar.setdefault(v["hangar_id"], []).append(v)
    out: dict[str, dict[str, float | None]] = {}
    for hid, vlist in by_hangar.items():
        out[hid] = {}
        for f in fields:
            means = [v["fields"][f]["mean"] for v in vlist
                     if v["fields"].get(f) and v["fields"][f]["mean"] is not None]
            out[hid][f] = _round(float(np.mean(means))) if means else None
    return out


def window_series(df: pd.DataFrame, field: str, window: str = "10min") -> pd.DataFrame:
    """时间窗聚合序列（用于曲线图与持续性判定）。

    同一窗内多航次样本直接平均（窗内近似同区域连续采样）。
    返回 timestamp / mean / count 三列。
    """
    g = (df.set_index("timestamp")[field]
         .resample(window).agg(["mean", "count"]).dropna())
    g = g[g["count"] > 0]
    return g.reset_index()
