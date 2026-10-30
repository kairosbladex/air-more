"""Validate and snapshot the versioned regional bulletin input bundle."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
FIELDS = ("so2", "no2", "co", "o3", "pm10", "pm2_5")
LABELS = dict(zip(FIELDS, ("SO2", "NO2", "CO", "O3", "PM10", "PM2.5")))
LABELS["composite"] = "综合指数"
UNITS = {f: "μg/m³" for f in FIELDS}
UNITS.update(co="mg/m³", composite="1")
KINDS = {"synthetic", "reference_transcription", "business_import", "grid_model"}
STAT_COLUMNS = {"id", "entity_id", "period", "start", "end", "metric", "unit",
                "value", "quality", "reason", "source_id", "basis"}
RANK_COLUMNS = (STAT_COLUMNS - {"value", "unit"}) | {
    "rank", "total", "scope_id", "scope_name"}
CONTRIBUTION_COLUMNS = (STAT_COLUMNS - {"unit"}) | {"mode"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def text(value, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{label}: required text")
    require(not any(ord(c) < 32 for c in value), f"{label}: control character")
    return value.strip()


def identifier(value, label: str) -> str:
    original = value
    value = text(value, label)
    require(original == value and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value)),
            f"{label}: invalid identifier")
    return value


def day(value: str) -> date:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)),
            f"invalid date: {value}")
    return date.fromisoformat(value)


def instant(value: str) -> datetime:
    require(isinstance(value, str), "timestamp must be ISO8601 text")
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, f"timestamp requires timezone: {value}")
    return result.astimezone(TZ)


def number(value, label: str, integer: bool = False, minimum: float = 0) -> float | int:
    require(not isinstance(value, bool) and value is not None, f"{label}: invalid number")
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid number") from exc
    require(math.isfinite(n) and n >= minimum, f"{label}: nonfinite or out of range")
    if integer:
        require(n.is_integer(), f"{label}: must be an integer")
        return int(n)
    return n


def _json(data: bytes):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, f"duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda v: require(False, f"invalid JSON constant: {v}"))


@dataclass
class Bundle:
    manifest: dict
    statistics: list[dict]
    ranks: list[dict]
    contributions: list[dict]
    forecasts: list[dict]
    advice: list[dict]
    images: list[dict]
    inputs: list[dict]
    assets: dict[str, bytes]

    @property
    def report_day(self) -> date:
        return day(self.manifest["report_date"])

    @property
    def stations(self) -> list[dict]:
        return sorted(self.manifest["stations"], key=lambda s: (s["order"], s["id"]))

    def period(self, kind: str) -> tuple[str, str]:
        end = self.report_day - timedelta(days=1)
        start = end if kind == "day" else end.replace(day=1)
        return start.isoformat(), end.isoformat()

    def evidence(self) -> dict:
        return {"schema_version": "1.0", "template_version": "regional-1.0",
                "manifest": self.manifest, "inputs": self.inputs,
                "statistics": self.statistics, "ranks": self.ranks,
                "contributions": self.contributions, "forecasts": self.forecasts,
                "advice": self.advice, "weather_images": self.images,
                "periods": {p: list(self.period(p)) for p in ("day", "month")}}


def load_bundle(path: str | Path) -> Bundle:
    path = Path(path).resolve()
    root = path.parent
    inputs, assets = [], {}

    def read(relative: str) -> bytes:
        relative = text(relative, "input path")
        p = (root / relative).resolve()
        require(p.is_relative_to(root), f"input outside bundle: {relative}")
        require(p.is_file(), f"input missing: {relative}")
        payload = p.read_bytes()
        if relative not in assets:
            assets[relative] = payload
            inputs.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest(),
                           "bytes": len(payload)})
        return payload

    m = _json(read(path.name))
    require(isinstance(m, dict) and m.get("schema_version") == "1.0", "unsupported schema_version")
    require(isinstance(m.get("region"), dict), "region must be an object")
    require(isinstance(m.get("sources"), list) and all(isinstance(s, dict) for s in m["sources"]),
            "sources must be an array of objects")
    require(isinstance(m.get("stations"), list) and all(isinstance(s, dict) for s in m["stations"]),
            "stations must be an array of objects")
    require(isinstance(m.get("files", {}), dict), "files must be an object")
    require(set(m.get("files", {})) <= {"statistics", "ranks", "contributions", "forecasts", "advice"},
            "unknown files key")
    require(m.get("data_kind") in KINDS, "invalid data_kind")
    identifier(m["region"]["id"], "region.id")
    text(m["region"]["name"], "region.name")
    identifier(m["issue"], "issue")
    d = day(m["report_date"])
    cutoff = instant(m["published_at"])
    require(cutoff.date() == d, "published_at must fall on report_date in Beijing time")
    sources = {}
    for source in m["sources"]:
        sid = identifier(source["id"], "source.id")
        require(sid not in sources, f"duplicate source: {sid}")
        require(source["kind"] == m["data_kind"], "mixed source kinds are not allowed")
        text(source["label"], "source.label")
        require(instant(source["issued_at"]) <= cutoff, "source issued after publication cutoff")
        sources[sid] = source
    require(bool(sources), "at least one source required")
    entities = {}
    for entity in m["stations"]:
        eid = identifier(entity["id"], "station.id")
        require(eid not in entities, f"duplicate station: {eid}")
        text(entity["name"], "station.name")
        require(entity["region_id"] == m["region"]["id"], "station region mismatch")
        entity["order"] = number(entity["order"], "station.order", integer=True)
        require(type(entity.get("focus")) is bool, "station.focus must be boolean")
        require(entity.get("kind") in {"station", "park", "combined"}, "invalid station.kind")
        entities[eid] = entity
    require(bool(entities), "at least one station required")
    b = Bundle(m, [], [], [], [], [], [], inputs, assets)
    ids, seen = set(), set()

    def source(record):
        require(record.get("source_id") in sources, "unknown source_id")

    def quality(record):
        require(record.get("quality") in {"valid", "missing"}, "quality must be valid or missing")
        if record["quality"] == "missing":
            text(record.get("reason"), "missing reason")

    def record_id(record):
        rid = identifier(record["id"], "record.id")
        require(rid not in ids, f"duplicate record id: {rid}")
        ids.add(rid)

    def read_rows(name, columns):
        filename = m.get("files", {}).get(name)
        if not filename:
            return []
        reader = csv.DictReader(io.StringIO(read(filename).decode("utf-8-sig")))
        require(reader.fieldnames is not None and set(reader.fieldnames) == columns
                and len(reader.fieldnames) == len(columns), f"{name}: CSV columns mismatch")
        rows = list(reader)
        for row in rows:
            require(None not in row and all(v is not None for v in row.values()),
                    f"{name}: malformed CSV row")
            record_id(row)
            require(row["entity_id"] in entities, "unknown entity_id")
            require(row["period"] in {"day", "month"}, "invalid period")
            require((row["start"], row["end"]) == b.period(row["period"]),
                    f"{row['id']}: statistics period mismatch")
            require(row["metric"] in UNITS, "unknown metric")
            source(row)
            quality(row)
            text(row["basis"], "statistical basis")
            key = (name, row["entity_id"], row["period"], row["metric"], row.get("scope_id"))
            require(key not in seen, f"duplicate result: {key}")
            seen.add(key)
        return rows

    b.statistics = read_rows("statistics", STAT_COLUMNS)
    for r in b.statistics:
        require(r["unit"] == UNITS[r["metric"]], f"{r['id']}: unit mismatch")
        require(r["quality"] != "missing" or r["value"] == "", "missing statistic has a value")
        r["value"] = number(r["value"], r["id"]) if r["quality"] == "valid" else None
    b.ranks = read_rows("ranks", RANK_COLUMNS)
    scopes = {}
    for r in b.ranks:
        identifier(r["scope_id"], "scope_id")
        text(r["scope_name"], "scope_name")
        r["total"] = number(r["total"], "total", integer=True, minimum=1) if r["total"] else None
        require(r["quality"] != "missing" or not r["rank"], "missing rank has a value")
        r["rank"] = number(r["rank"], "rank", integer=True, minimum=1) if r["quality"] == "valid" else None
        require(r["rank"] is None or r["total"] is None or r["rank"] <= r["total"], "rank exceeds total")
        k = (r["period"], r["metric"], r["scope_id"])
        signature = (r["scope_name"], r["total"], r["basis"], r["source_id"])
        require(k not in scopes or scopes[k] == signature, "inconsistent rank scope")
        scopes[k] = signature
    identifier(m["table_scope_id"], "table_scope_id")
    b.contributions = read_rows("contributions", CONTRIBUTION_COLUMNS)
    groups = {}
    for r in b.contributions:
        require(r["metric"] in FIELDS, "contribution metric must be a pollutant")
        require(r["mode"] in {"components", "ratios"}, "invalid contribution mode")
        require(r["quality"] != "missing" or not r["value"], "missing contribution has a value")
        r["value"] = number(r["value"], r["id"]) if r["quality"] == "valid" else None
        groups.setdefault((r["entity_id"], r["period"]), []).append(r)
    for rows in groups.values():
        require(len({(r["mode"], r["source_id"], r["basis"]) for r in rows}) == 1,
                "mixed contribution mode, source or basis")
        if rows[0]["mode"] == "ratios":
            require(all(r["value"] is None or r["value"] <= 1 for r in rows), "ratio outside [0,1]")
            if len(rows) == 6 and all(r["value"] is not None for r in rows):
                total = sum(r["value"] for r in rows)
                require(total == 0 or abs(total - 1) <= 0.000001, "ratios must sum to one")

    def json_rows(name):
        filename = m.get("files", {}).get(name)
        rows = _json(read(filename)) if filename else []
        require(isinstance(rows, list), f"{name}: expected JSON array")
        for r in rows:
            require(isinstance(r, dict), f"{name}: records must be objects")
            record_id(r)
            source(r)
            quality(r)
            require(instant(r["issued_at"]) <= cutoff, f"{name}: issued after cutoff")
        return rows

    b.forecasts = json_rows("forecasts")
    forecast_days = set()
    for r in b.forecasts:
        text(r["basis"], "forecast basis")
        require(r["region_id"] == m["region"]["id"], "forecast region mismatch")
        valid = day(r["date"])
        require(d <= valid <= d + timedelta(days=4), "forecast outside report horizon")
        require(r["date"] not in forecast_days, "duplicate forecast date")
        forecast_days.add(r["date"])
        for low, high, minimum, maximum in [
            ("aqi_min", "aqi_max", 0, 500), ("temp_min", "temp_max", -100, 100),
            ("humidity_min", "humidity_max", 0, 100), ("wind_min", "wind_max", 0, 17)]:
            require(low in r and high in r, f"forecast requires {low}/{high}, use null when missing")
            require((r[low] is None) == (r[high] is None), "incomplete forecast range")
            if r[low] is not None:
                r[low] = number(r[low], low, integer=low.startswith(("aqi", "wind")), minimum=minimum)
                r[high] = number(r[high], high, integer=low.startswith(("aqi", "wind")), minimum=minimum)
                require(r[low] <= r[high] <= maximum, f"invalid range: {low}/{high}")
        for name in ("category", "weather", "wind_direction"):
            require(name in r and (r[name] is None or isinstance(r[name], str)), f"invalid {name}")
            if r[name] is not None:
                text(r[name], name)
                require(not re.search(r"[0-9]", r[name]), f"{name}: use structured numeric fields")
        require(isinstance(r["primary_pollutants"], list)
                and all(f in FIELDS for f in r["primary_pollutants"]), "invalid primary_pollutants")
        if r["quality"] == "missing":
            require(all(r[f] is None for f in ("aqi_min", "temp_min", "humidity_min", "wind_min",
                                              "category", "weather", "wind_direction"))
                    and not r["primary_pollutants"], "missing forecast contains values")
        if not r["primary_pollutants"] or any(r[f] is None for f in (
                "aqi_min", "temp_min", "humidity_min", "wind_min", "category", "weather", "wind_direction")):
            text(r.get("reason"), "partial forecast reason")

    b.advice = json_rows("advice")
    fact_ids = {r["id"] for r in b.statistics + b.ranks if r["quality"] == "valid"}
    advice_entities = set()
    for r in b.advice:
        require(r["entity_id"] in entities and r["entity_id"] not in advice_entities,
                "unknown or duplicate advice entity")
        advice_entities.add(r["entity_id"])
        text(r["text"], "advice.text")
        require(isinstance(r["evidence_ids"], list) and set(r["evidence_ids"]) <= fact_ids,
                "advice references unknown or missing fact")
        referenced = [v for v in b.statistics + b.ranks if v["id"] in r["evidence_ids"]]
        require(all(v["entity_id"] == r["entity_id"] for v in referenced), "advice entity mismatch")
        # Numerical advice must use fact placeholders, never an unbound number.
        tokens = re.findall(r"\{([A-Za-z0-9_-]+)\}", r["text"])
        require(set(tokens) <= set(r["evidence_ids"]), "unbound advice placeholder")
        residue = re.sub(r"\{[A-Za-z0-9_-]+\}", "", r["text"])
        for label in LABELS.values():
            residue = residue.replace(label, "")
        require(not re.search(r"[0-9{}]", residue), "advice numbers require fact placeholders")
        require(not re.search(r"[零〇一二两三四五六七八九十百千万]+(?:名|位|天|小时|分钟|%|级|点)", residue),
                "advice numerical claims require fact placeholders")
    b.images = m.get("weather_images", [])
    require(isinstance(b.images, list), "weather_images must be an array")
    for r in b.images:
        require(isinstance(r, dict), "weather image records must be objects")
        record_id(r)
        source(r)
        text(r["caption"], "image caption")
        require(r["region_id"] == m["region"]["id"], "image region mismatch")
        start, end = instant(r["valid_start"]), instant(r["valid_end"])
        require(instant(r["issued_at"]) <= cutoff, "image issued after cutoff")
        require(start < end and start < datetime.combine(d + timedelta(days=5), time(), TZ)
                and end > datetime.combine(d, time(), TZ), "weather image outside report horizon")
        payload = read(r["path"])
        from PIL import Image
        with Image.open(io.BytesIO(payload)) as img:
            require(img.format in {"PNG", "JPEG"}, "weather image must be PNG or JPEG")
            img.verify()
    return b
