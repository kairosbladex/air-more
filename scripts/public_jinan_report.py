"""Fetch and reproduce the 2026-09-09 Jinan public-data bulletin."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lxml import html
from matplotlib import pyplot as plt, font_manager

from airmore.regional.bundle import Bundle, TZ, require
from airmore.regional.render import convert_pdf, preflight, render_docx, verify_docx

DAY = date(2026, 9, 9)
CITIES = {370100: "济南市", 370300: "淄博市", 370500: "东营市", 370700: "潍坊市",
          370900: "泰安市", 371400: "德州市", 371500: "聊城市", 371600: "滨州市"}
FOCUS = (370100, 370300)
BASE = "https://air.cnemc.cn:18007/"
OUTLOOK = "https://www.mee.gov.cn/ywdt/xwfb/202609/t20260906_1165222.shtml"
MET_PARAMS = {"latitude": 36.65, "longitude": 117.12,
              "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation",
              "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,wind_direction_10m_dominant",
              "timezone": "Asia/Shanghai", "start_date": "2026-09-09", "end_date": "2026-09-13",
              "wind_speed_unit": "ms"}
MET_URL = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(MET_PARAMS)
FIELDS = ("SO2_24h", "NO2_24h", "CO_24h", "O3_8h_24h", "PM10_24h", "PM2_5_24h")
LEVELS = ("优", "良", "轻度污染", "中度污染", "重度污染", "严重污染")


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def fetch_snapshot(root: Path):
    require(not root.exists(), f"snapshot already exists: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".public-source-", dir=root.parent))
    sources = []
    try:
        requests = [("city_daily.json", BASE + "CityData/GetAllCityDayAQIModels", "GET"),
                    ("outlook.html", OUTLOOK, "GET"), ("weather.json", MET_URL, "GET")]
        requests += [(f"city_{code}.json", BASE + f"HourChangesPublish/GetCityDayAqiHistoryByCondition?citycode={code}", "POST")
                     for code in CITIES]
        for name, url, method in requests:
            request = urllib.request.Request(url, data=b"" if method == "POST" else None,
                                             headers={"User-Agent": "air-more-public-report/1.0"})
            last = None
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        payload = response.read()
                        response_type = response.headers.get("Content-Type")
                    require(bool(payload), f"empty source: {url}")
                    if name.endswith(".json"):
                        json.loads(payload)
                    break
                except Exception as exc:
                    last = exc
            else:
                raise RuntimeError(f"fetch failed: {url}") from last
            (staging / name).write_bytes(payload)
            sources.append({"file": name, "url": url, "method": method,
                            "retrieved_at": datetime.now(TZ).isoformat(), "sha256": digest(payload),
                            "content_type": response_type})
            print(f"Fetched {name} ({len(payload)} bytes)", flush=True)
        write_json(staging / "sources.json", sources)
        staging.rename(root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def record_date(record):
    match = re.fullmatch(r"/Date\((\d+)\)/", record["TimePoint"])
    require(match is not None, "invalid monitoring timestamp")
    return datetime.fromtimestamp(int(match[1]) / 1000, TZ).date()


def numeric(value):
    require(not isinstance(value, bool), "boolean is not a measurement")
    n = float(value)
    require(math.isfinite(n) and n >= 0, "invalid monitoring value")
    return n


def validate_history(records, code):
    require(isinstance(records, list), "history must be an array")
    by_day = {}
    for row in records:
        dt = record_date(row)
        require(row["CityCode"] == code and row["Area"] == CITIES[code], "wrong city")
        require(dt not in by_day, "duplicate daily record")
        for field in (*FIELDS, "AQI"):
            numeric(row[field])
        require(numeric(row["AQI"]) <= 500 and numeric(row["AQI"]).is_integer(), "invalid AQI")
        require(row["Quality"] in LEVELS, "unknown quality category")
        by_day[dt] = row
    end = DAY - timedelta(days=1)
    dates = [end.replace(day=1) + timedelta(days=i) for i in range(end.day)]
    require(all(d in by_day for d in dates), "incomplete month-to-date; refusing full-month claim")
    return [by_day[d] | {"date": d.isoformat()} for d in dates]


def month_summary(rows):
    counts = {level: sum(r["Quality"] == level for r in rows) for level in LEVELS}
    good = counts["优"] + counts["良"]
    return {"days": len(rows), "good_days": good, "good_percent": 100 * good / len(rows),
            "counts": counts, "aqi_min": min(numeric(r["AQI"]) for r in rows),
            "aqi_max": max(numeric(r["AQI"]) for r in rows),
            "pm25_mean": sum(numeric(r["PM2_5_24h"]) for r in rows) / len(rows),
            "pm10_mean": sum(numeric(r["PM10_24h"]) for r in rows) / len(rows),
            "ozone_pollution_days": sum("O3" in r["PrimaryPollutant"] and numeric(r["AQI"]) > 100 for r in rows)}


def load_sources(root):
    metadata = json.loads((root / "sources.json").read_text())
    require(len(metadata) == 11, "source list incomplete")
    for source in metadata:
        require(digest((root / source["file"]).read_bytes()) == source["sha256"], "source hash changed")
    histories = {code: validate_history(json.loads((root / f"city_{code}.json").read_text()), code) for code in CITIES}
    daily = json.loads((root / "city_daily.json").read_text())
    for code, rows in histories.items():
        summary = [r for r in daily if r["CityCode"] == code]
        require(len(summary) == 1, "duplicate or missing city summary")
        row = summary[0]
        require(row["TimePoint"][:10] == rows[-1]["date"], "latest daily endpoint date differs")
        for field in ("AQI", "Quality", "PrimaryPollutant"):
            require(row[field] == rows[-1][field], "daily/history endpoint disagreement")
    tree = html.fromstring((root / "outlook.html").read_text())
    publication = tree.xpath('//meta[@name="PubDate"]/@content')[0]
    require(date.fromisoformat(publication[:10]) <= DAY, "outlook published after report date")
    paragraphs = [p.text_content().strip() for p in tree.xpath('//div[contains(@class,"TRS_Editor")]/*')]
    matching = [p for p in paragraphs if p.startswith("京津冀及周边地区：")]
    require(len(matching) == 1, "regional forecast paragraph missing or duplicated")
    region = matching[0]
    for expected in ("9—10日，转受静小风控制", "山东西部和河南中南部个别城市可能出现轻度污染",
                     "11—15日，受高压过境影响", "首要污染物为O3或PM2.5"):
        require(expected in region, "outlook text changed; re-review required")
    weather = json.loads((root / "weather.json").read_text())
    require(weather["utc_offset_seconds"] == 28800, "weather timezone differs")
    require(abs(weather["latitude"] - MET_PARAMS["latitude"]) < .1 and
            abs(weather["longitude"] - MET_PARAMS["longitude"]) < .1, "weather grid differs")
    expected_dates = [(DAY + timedelta(days=i)).isoformat() for i in range(5)]
    require(weather["daily"]["time"] == expected_dates, "forecast dates differ")
    expected_times = [(datetime.combine(DAY, datetime.min.time()) + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(120)]
    require(weather["hourly"]["time"] == expected_times, "weather hours incomplete")
    units = {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "m/s",
             "wind_direction_10m": "°", "precipitation": "mm"}
    for field, unit in units.items():
        require(weather["hourly_units"][field] == unit, "weather unit differs")
        values = weather["hourly"][field]
        require(len(values) == 120 and all(isinstance(v, (float, int)) and math.isfinite(v) for v in values), "missing weather hour")
    for field, values in weather["daily"].items():
        require(len(values) == 5, "missing weather day")
        if field != "time":
            require(all(isinstance(v, (int, float)) and math.isfinite(v) for v in values), "invalid weather day")
    daily_units = {"temperature_2m_min": "°C", "temperature_2m_max": "°C", "precipitation_sum": "mm",
                   "wind_speed_10m_max": "m/s", "wind_direction_10m_dominant": "°", "weather_code": "wmo code"}
    for field, unit in daily_units.items():
        require(weather["daily_units"][field] == unit, "daily weather unit differs")
    require(all(0 <= v <= 100 for v in weather["hourly"]["relative_humidity_2m"]), "humidity out of range")
    require(all(a <= b for a, b in zip(weather["daily"]["temperature_2m_min"], weather["daily"]["temperature_2m_max"])), "temperature range reversed")
    return metadata, histories, weather, {"publication": publication, "paragraph": region}


def png(fig):
    stream = io.BytesIO()
    fig.savefig(stream, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    return stream.getvalue()


def make_images(histories, weather, runtime):
    font = font_manager.FontProperties(fname=runtime["font_path"])
    assets = {}
    with plt.rc_context({"font.family": runtime["font_family"], "axes.unicode_minus": False}):
        for code in FOCUS:
            rows = histories[code]
            fig, ax = plt.subplots(figsize=(5, 3.2))
            values = [numeric(r["AQI"]) for r in rows]
            ax.plot(range(1, 9), values, color="#307453", marker="o", linewidth=1.6)
            for i, value in enumerate(values, 1):
                ax.annotate(f"{value:g}", (i, value), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=8)
            ax.axhline(100, color="#b18c25", linestyle="--", linewidth=.8)
            ax.set_ylim(0, max(130, max(values) + 30))
            ax.set_xticks(range(1, 9), [f"9/{i}" for i in range(1, 9)])
            ax.set_ylabel("AQI")
            ax.set_title(CITIES[code] + "本月逐日 AQI", fontproperties=font, fontsize=12)
            ax.grid(axis="y", alpha=.2)
            fig.tight_layout()
            assets[f"aqi-{code}.png"] = png(fig)
        h = weather["hourly"]
        specs = [("temp-rh.png", "济南气温与相对湿度预报", "temperature_2m", "气温（℃）", "relative_humidity_2m", "相对湿度（%）"),
                 ("wind-rain.png", "济南风速与降水预报", "wind_speed_10m", "风速（m/s）", "precipitation", "降水（mm）")]
        for name, title, left, ylabel, right, rightlabel in specs:
            fig, ax = plt.subplots(figsize=(5, 3.2))
            ax.plot(range(120), h[left], color="#307453", linewidth=1.3)
            ax.set_ylabel(ylabel, fontproperties=font, fontsize=9)
            ax.yaxis.label.set_color("#307453")
            secondary = ax.twinx()
            secondary.plot(range(120), h[right], color="#497caa", linewidth=1, alpha=.85)
            secondary.set_ylabel(rightlabel, fontproperties=font, fontsize=9)
            secondary.yaxis.label.set_color("#497caa")
            ax.set_xticks(range(0, 120, 24), [f"9/{i}" for i in range(9, 14)])
            ax.set_title(title, fontproperties=font, fontsize=12)
            ax.grid(alpha=.18)
            fig.tight_layout()
            assets[name] = png(fig)
    return assets


def weather_name(code):
    names = {0: "晴", 1: "少云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
             51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨", 56: "轻冻毛毛雨", 57: "强冻毛毛雨",
             61: "小雨", 63: "中雨", 65: "大雨", 66: "轻冻雨", 67: "强冻雨",
             71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪", 80: "小阵雨", 81: "中阵雨", 82: "强阵雨",
             85: "小阵雪", 86: "强阵雪", 95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹"}
    require(code in names, "unmapped WMO weather code")
    return names[code]


def wind_name(degrees):
    return ("北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风")[int((degrees + 22.5) // 45) % 8]


def advice_text(histories, summaries):
    facts = []
    for code in FOCUS:
        s, r = summaries[code], histories[code][-1]
        facts.append(f"{CITIES[code][:-1]}本月优良{s['good_days']}天，优良率{s['good_percent']:.1f}%，"
                     f"昨日AQI为{r['AQI']}，空气质量为{r['Quality']}")
    ozone = (f"两市本月分别出现{summaries[FOCUS[0]]['ozone_pollution_days']}天、"
             f"{summaries[FOCUS[1]]['ozone_pollution_days']}天臭氧为首要污染物的污染天气")
    return ("截至9月8日，" + "；".join(facts) + "。" + ozone + "。"
            "9—10日关注静小风条件下的污染积累，济南、淄博重点检查工业涂装、包装印刷等涉VOCs企业废气收集和治理设施运行情况，"
            "加强午后臭氧监测；道路保洁和施工扬尘管控按日常要求落实。")


def build_view(histories, weather, outlook, metadata):
    summaries = {code: month_summary(rows) for code, rows in histories.items()}
    blocks = []
    def p(text, kind="paragraph", page_break=False):
        blocks.append({"type": kind, "text": text, "page_break_before": page_break})
    def table(header, rows, widths, focus_rows=()):
        blocks.append({"type": "table", "header": header, "rows": rows, "widths": widths,
                       "styles": [{"fill": "FFFF00" if i in focus_rows else None} for i in range(len(rows))]})
    def pair(ids, captions):
        blocks.append({"type": "weather", "ids": ids, "captions": captions})
    p("环境空气质量研判日报", "title")
    p("济南及周边城市  第〔2026〕0909期", "issue")
    p("2026年9月9日", "date")
    p("监测数据采用中国环境监测总站公开结果，统计截止9月8日。", "note")
    p("一、区域空气质量情况", "section")
    p("昨日空气质量情况统计表（9月8日）", "caption")
    rows = []
    for code, city in CITIES.items():
        row = histories[code][-1]
        rows.append([city[:-1], *[row[f] for f in FIELDS], row["AQI"], row["Quality"]])
    table(["城市", "SO2", "NO2", "CO", "O3-8h", "PM10", "PM2.5", "AQI", "等级"], rows,
          [1.7, 1.3, 1.3, 1.15, 1.7, 1.55, 1.65, 1.3, 1.65], (0, 1))
    p("注：CO单位为mg/m³，其余污染物为μg/m³；O3-8h为臭氧日最大8小时平均浓度，其余为日均浓度。黄色标注济南、淄博，表格按城市代码排列。", "note")
    p("济南、淄博本月空气质量变化", "caption")
    pair([f"aqi-{c}.png" for c in FOCUS], ["9月1日至8日城市日AQI；虚线为AQI=100。"] * 2)
    p("当月累计空气质量情况（9月1日至8日）", "caption", page_break=True)
    rows = []
    for code, city in CITIES.items():
        s = summaries[code]
        rows.append([city[:-1], str(s["good_days"]), f"{s['good_percent']:.1f}",
                     str(s["days"] - s["good_days"]), f"{s['pm25_mean']:.1f}", f"{s['pm10_mean']:.1f}",
                     f"{s['aqi_min']:g}—{s['aqi_max']:g}"])
    table(["城市", "优良\n天数", "优良率\n（%）", "污染\n天数", "PM2.5\n均值", "PM10\n均值", "AQI范围"],
          rows, [1.7, 1.6, 2.0, 1.6, 2.1, 2.1, 2.2], (0, 1))
    p("注：8天公开日记录完整。优良天数按公开等级为优或良计数；颗粒物均值为这8个日均值的算术平均，单位μg/m³；AQI范围为本期日AQI最小值至最大值，不是预测区间。", "note")
    p("上述累计数为公开日记录汇总。所用数据源未提供本期正式综合指数、城市排名及园区数据，本报不作推算。", "note")
    p("二、环境空气质量预测", "section")
    p("9月9—10日，京津冀及周边地区转受静小风控制，区域大部空气质量以优良为主，山东西部和河南中南部个别城市可能出现轻度污染。9月11—15日，受高压过境影响，以偏北风为主，区域大部空气质量以优良为主。首要污染物为O3或PM2.5。")
    p("来源：生态环境部9月上半月预报会商结果，9月6日发布。以上为区域预报，原文未发布济南、淄博逐日AQI数值区间。", "note")
    p("数据来源与口径", "heading")
    fetched = datetime.fromisoformat(next(s["retrieved_at"] for s in metadata if s["file"] == "weather.json"))
    p("监测：中国环境监测总站全国城市空气质量实时发布平台，城市日报及逐日历史接口。预报：生态环境部《9月上半月全国空气质量预报会商结果》。", "note")
    p(f"气象：Open-Meteo，取数时间{fetched.month}月{fetched.day}日{fetched:%H:%M}（北京时间），返回网格{weather['latitude']:.5f}°N、{weather['longitude']:.5f}°E。接口未返回模型起报时刻，取数时刻不作为起报时刻。", "note")
    p("监测与预报分别使用各自来源，原始响应、链接、汇总公式及文件校验值随证据JSON留存。建议为本报研判意见。", "note")
    p("三、空气污染气象条件", "section", page_break=True)
    p("济南未来五天气象预报（9月9日至13日）", "caption")
    d, h = weather["daily"], weather["hourly"]
    data = []
    for i, dt in enumerate(d["time"]):
        humidity = h["relative_humidity_2m"][i * 24:(i + 1) * 24]
        data.append([dt[5:].replace("-", "/"), weather_name(d["weather_code"][i]),
                     f"{d['temperature_2m_min'][i]:.1f}—{d['temperature_2m_max'][i]:.1f}",
                     f"{min(humidity):g}—{max(humidity):g}", wind_name(d["wind_direction_10m_dominant"][i]),
                     f"{d['wind_speed_10m_max'][i]:.1f}", f"{d['precipitation_sum'][i]:.1f}"])
    labels = ["日期", "天气", "气温（℃）", "湿度（%）", "主导风向", "最大风速\n（m/s）", "降水（mm）"]
    table(["项目", *[r[0] for r in data]], [[labels[j], *[r[j] for r in data]] for j in range(1, 7)], [2.3] + [2.25] * 5)
    p("气象数值来自Open-Meteo自动选择模型的济南网格预报，非地面实测；风速为10米风速。湿度范围按每日24个小时值汇总，气温、降水和最大风速采用接口日值。", "note")
    pair(["temp-rh.png", "wind-rain.png"], ["9月9日00时至13日23时，气温与湿度逐小时模型预报。",
                                                  "9月9日00时至13日23时，风速与降水逐小时模型预报。"])
    p("四、工作建议", "section")
    p(advice_text(histories, summaries))
    return {"blocks": blocks, "charts": [], "disclaimer": "济南及周边城市空气质量研判日报"}, summaries


def generate(snapshot: Path, out: Path):
    metadata, histories, weather, outlook = load_sources(snapshot)
    runtime = preflight()
    view, summaries = build_view(histories, weather, outlook, metadata)
    assets = make_images(histories, weather, runtime)
    bundle = Bundle({}, [], [], [], [], [], [{"id": name, "path": name} for name in assets], [], assets)
    stem = "济南及周边城市空气质量研判日报_2026-09-09"
    require(not out.exists(), f"output exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".public-bulletin-", dir=out.parent))
    try:
        docx = staging / (stem + ".docx")
        media = render_docx(bundle, view, docx, runtime)
        verify_docx(docx, view, media)
        pdf = convert_pdf(docx, runtime)
        evidence = {"report_date": DAY.isoformat(), "template": "public-jinan-1.0", "status": "complete_with_source_limits",
                    "sources": metadata, "monitoring_days": {str(k): v for k, v in histories.items()},
                    "month_summary": {str(k): v for k, v in summaries.items()}, "regional_forecast": outlook,
                    "weather": weather, "view": view, "source_scope": "public_station_observations_and_separate_weather_model",
                    "month_formula": "count(source Quality in 优/良); mean(source daily PM2.5/PM10); min/max(source daily AQI)",
                    "omissions": ["official composite index and ranking", "park observations", "city-specific numeric AQI forecast", "weather model initialization time"],
                    "recommendation_basis": {"kind": "editorial_judgment", "facts": [f"city_{c}.json" for c in FOCUS],
                                             "outlook_source": "outlook.html", "not_official_control_order": True},
                    "artifact_sha256": {p.name: digest(p.read_bytes()) for p in (docx, pdf)},
                    "media_sha256": {k: digest(v) for k, v in media.items()},
                    "runtime": {k: v for k, v in runtime.items() if k != "env"}}
        write_json(staging / (stem + ".json"), evidence)
        staging.rename(out)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps({"output": str(out.resolve()), "recommendation": advice_text(histories, summaries)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Fetch new immutable source snapshot")
    args = parser.parse_args()
    if args.fetch:
        fetch_snapshot(args.snapshot)
    generate(args.snapshot, args.out)
