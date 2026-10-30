"""Explicitly synthetic fixtures for repeatable integration and GPU smoke tests."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from .models import Dataset, METRICS, ReportRequest, UNITS, dates


def synthetic_case(count=2, missing=False, *, start_date="2026-08-10", end_date="2026-08-12", report_type="MONTH"):
    request = ReportRequest(project_id="synthetic-project", hangar_ids=[f"demo-{i+1}" for i in range(count)],
                            start_date=start_date, end_date=end_date, report_type=report_type,
                            requester_id="synthetic-reader")
    rows, profiles, hangars = [], {}, {}
    for i, hid in enumerate(request.hangar_ids):
        hangars[hid] = f"测试机库{chr(65+i)}"
        key = f"synthetic-project:synthetic:sensor-{i}"
        profiles[key] = {"mode": "fixed_point", "method_confirmed": True, "interval_mean_confirmed": True,
                         "basis": "合成数据，仅用于测试程序，不表示仪器或业务已验收", "hangar_id": hid,
                         "sample_interval_seconds": 60, "location": {"lon": 0, "lat": 0, "tolerance_m": 10},
                         "units": {m: {"unit": UNITS[m], "confirmed": True,
                                       "state": "ambient" if m in {"pm10", "pm2_5"} else "reference_298.15K_101.325kPa"}
                                   for m in METRICS}}
        for day in dates(request.comparison()[0], request.end_date):
            for minute in range(1440 if not missing else 30):
                stamp = datetime.combine(day, time()) + timedelta(minutes=minute)
                hour = minute // 60
                values = {"so2": 8 + i, "no2": 22 + i + hour / 4, "co": .4 + i * .1,
                          "o3": 80 + hour * 3, "pm10": 35 + day.day % 5 + i,
                          "pm2_5": 22 + day.day % 5 + i * 6}
                if missing:
                    values["o3"] = -1
                rows.append({"id": f"s-{len(rows)}", "project_id": request.project_id,
                             "task_id": f"task-{i}-{day}", "hangar_id": hid, "load_sn": f"sensor-{i}",
                             "load_type": "synthetic", "time": stamp, "lon": 0, "lat": 0, "alt": 1, **values})
    return request, Dataset(rows, hangars, data_kind="synthetic"), profiles
