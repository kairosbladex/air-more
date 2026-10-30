"""Bounded, parameterized reads; never migrations, DDL or business writes."""
from __future__ import annotations

import hashlib
from datetime import datetime
from contextlib import contextmanager

import pymysql

from .config import Settings
from .models import Dataset, METRICS, ReportError, ReportRequest, TZ


class MySQLRepository:
    def __init__(self, settings: Settings, connect=None):
        self.cfg = settings
        self._connect = connect or pymysql.connect

    @contextmanager
    def session(self):
        conn = None
        try:
            conn = self._connect(host=self.cfg.db_host, port=self.cfg.db_port,
                                 user=self.cfg.db_user, password=self.cfg.db_password,
                                 database=self.cfg.db_name, charset="utf8mb4",
                                 connect_timeout=8, read_timeout=max(10, self.cfg.query_timeout_ms // 1000 + 5),
                                 write_timeout=10, autocommit=False,
                                 cursorclass=pymysql.cursors.DictCursor)
            with conn.cursor() as cur:
                cur.execute("SET SESSION time_zone = '+08:00'")
                cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (self.cfg.query_timeout_ms,))
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                yield cur
        except pymysql.MySQLError as exc:
            if exc.args and exc.args[0] in {1044, 1045, 1142, 1143, 1227}:
                raise ReportError("DATABASE_PERMISSION_DENIED", "数据库身份认证或读取权限被拒绝") from None
            raise ReportError("DATABASE_ERROR", "数据库读取失败或超时，请检查连接和只读权限") from None
        finally:
            if conn is not None:
                try:
                    conn.rollback()
                finally:
                    conn.close()

    def find_hangar_sites(self, name: str, project_id: str | None = None) -> list[str]:
        """Name is discovery only; require a unique hangar before using its site IDs."""
        with self.session() as cur:
            cur.execute("SELECT id,project_id FROM bo_uav_hangar WHERE name=%s AND del_flag=0"
                        + (" AND project_id=%s" if project_id else "") + " LIMIT 2",
                        (name, project_id) if project_id else (name,))
            candidates = cur.fetchall()
            if len(candidates) != 1:
                raise ReportError("HANGAR_NOT_FOUND" if not candidates else "AMBIGUOUS_HANGAR",
                                  "机库名称未找到或存在歧义，请指定项目与唯一机库")
            h = candidates[0]
            cur.execute("SELECT id FROM bo_uav_site WHERE hangar_id=%s AND project_id=%s AND del_flag=0 LIMIT %s",
                        (h["id"], h["project_id"], self.cfg.max_hangars + 1))
            sites = cur.fetchall()
        if not sites or len(sites) > self.cfg.max_hangars:
            raise ReportError("INVALID_HANGAR_SCOPE", "机库关联站点为空或超过上限")
        return sorted(r["id"] for r in sites)

    def resolve_project(self, site_ids: list[str]) -> str:
        """Resolve only current, active sites. Cross-project reports are rejected."""
        slots = ",".join(["%s"] * len(site_ids))
        with self.session() as cur:
            cur.execute(f"SELECT id,project_id FROM bo_uav_site WHERE del_flag=0 AND id IN ({slots})",
                        tuple(site_ids))
            rows = cur.fetchall()
        if {r["id"] for r in rows} != set(site_ids):
            raise ReportError("INVALID_HANGAR_SCOPE", "机库不存在、已删除或不在当前机库表中")
        projects = {r["project_id"] for r in rows}
        if len(projects) != 1 or not next(iter(projects)):
            raise ReportError("INVALID_PROJECT_SCOPE", "所选机库必须属于同一个有效项目")
        return next(iter(projects))

    def load(self, request: ReportRequest) -> Dataset:
        if request.as_of is None:
            request.as_of = datetime.now(TZ)
        start, end = request.query_bounds()
        slots = ",".join(["%s"] * len(request.hangar_ids))
        with self.session() as cur:
            # Current master data only. A historical backup is not an identity provider.
            cur.execute(f"""SELECT s.id,s.name,s.enable+0 AS enabled,
                h.id AS physical_hangar_id,h.name AS physical_hangar_name,h.project_id AS hangar_project,
                h.del_flag+0 AS hangar_deleted,u.id AS uav_id,u.project_id AS uav_project,
                u.del_flag+0 AS uav_deleted
                FROM bo_uav_site s LEFT JOIN bo_uav_hangar h ON h.id=s.hangar_id
                LEFT JOIN bo_uav_register_info u ON u.id=s.uav_id
                WHERE s.project_id=%s AND s.del_flag=0 AND s.id IN ({slots})""",
                        (request.project_id, *request.hangar_ids))
            bindings = list(cur.fetchall())
            hangars = {r["id"]: r["name"] for r in bindings}
            if len(hangars) != len(bindings):
                raise ReportError("RELATION_CARDINALITY_ERROR", "站点关联产生重复，拒绝放大采样")
            for binding in bindings:
                # Older production registries leave UAV.project_id empty.
                # Require exclusive active-site ownership; never overwrite a
                # conflicting explicit project or infer it from a device name.
                uav_project = binding.get("uav_project")
                if not uav_project and binding.get("uav_id"):
                    cur.execute("SELECT DISTINCT project_id FROM bo_uav_site "
                                "WHERE uav_id=%s AND del_flag=0 LIMIT 2", (binding["uav_id"],))
                    owners = {r["project_id"] for r in cur.fetchall()}
                    if owners == {request.project_id}:
                        uav_project = request.project_id
                        binding["uav_project_basis"] = "exclusive_active_site_project"
                if (not binding.get("physical_hangar_id") or not binding.get("uav_id")
                        or binding.get("hangar_deleted") != 0 or binding.get("uav_deleted") != 0
                        or binding.get("hangar_project") != request.project_id
                        or uav_project != request.project_id):
                    raise ReportError("INVALID_DEVICE_LINKAGE", "站点关联机库或无人机缺失、已删除或项目不一致")
                # Current enable/authentication flags are metadata, not historical measurement validity.

            if set(hangars) != set(request.hangar_ids):
                raise ReportError("INVALID_HANGAR_SCOPE", "所选机库不属于项目、已删除或不在当前机库表中")
            # Aggregate excluded rows within this project, never return unrelated identities.
            cur.execute("""SELECT CASE
                WHEN t.id IS NULL THEN 'missing_task'
                WHEN t.del_flag<>0 THEN 'deleted_task'
                WHEN NOT (t.project_id <=> a.project_id) THEN 'task_project_mismatch'
                WHEN s.id IS NULL THEN 'missing_hangar'
                WHEN s.del_flag<>0 THEN 'deleted_hangar'
                WHEN NOT (s.project_id <=> a.project_id) THEN 'hangar_project_mismatch'
                ELSE 'eligible' END AS reason, COUNT(*) AS n
                FROM bo_task_air a LEFT JOIN bo_task t ON t.id=a.task_id
                LEFT JOIN bo_uav_site s ON s.id=t.site_id
                WHERE a.project_id=%s AND a.del_flag=0 AND a.time>=%s AND a.time<%s
                GROUP BY reason""", (request.project_id, start, end))
            exclusions = {r["reason"]: int(r["n"]) for r in cur.fetchall() if r["reason"] != "eligible"}
            # LIMIT is a hard resource guard, not silent truncation. Indexed time filters.
            fields = ",".join("a." + m for m in METRICS)
            cur.execute(f"""SELECT a.id,a.project_id,a.task_id,a.load_sn,a.load_type,a.time,
                a.lon,a.lat,a.alt,{fields},s.id AS hangar_id
                FROM bo_task_air a JOIN bo_task t ON t.id=a.task_id
                JOIN bo_uav_site s ON s.id=t.site_id
                WHERE a.project_id=%s AND t.project_id=a.project_id AND s.project_id=a.project_id
                AND a.del_flag=0 AND t.del_flag=0 AND s.del_flag=0
                AND a.time>=%s AND a.time<%s AND s.id IN ({slots})
                ORDER BY a.time,a.id LIMIT %s""",
                        (request.project_id, start, end, *request.hangar_ids, self.cfg.max_rows + 1))
            rows = cur.fetchall()
            if len(rows) > self.cfg.max_rows:
                raise ReportError("TOO_MUCH_DATA", "采样数量超过单次上限，请缩小日期或机库范围")
            payloads = []
            if bindings:
                uav_ids = sorted({b["uav_id"] for b in bindings})
                uav_slots = ",".join(["%s"] * len(uav_ids))
                cur.execute(f"""SELECT id,uav_id,load_imei,load_name,load_type,load_type_name,
                    load_brand_code,load_brand_name,load_model,state FROM bo_load_register_info
                    WHERE del_flag=0 AND uav_id IN ({uav_slots}) LIMIT %s""",
                            (*uav_ids, self.cfg.max_rows + 1))
                payloads = list(cur.fetchall())
                if len(payloads) > self.cfg.max_rows:
                    raise ReportError("TOO_MUCH_DATA", "载荷登记数量超过读取上限")
            cur.execute("""SELECT index_name,index_unit,molecular_weight,sort
                FROM bo_air_quality_index_project
                WHERE project_id=%s AND del_flag=0 AND index_status=1""", (request.project_id,))
            units = cur.fetchall()
            if any('defaultUnitCode' in (r.get('index_unit') or '') for r in units):
                cur.execute("SELECT code,value FROM sys_dict_item "
                            "WHERE type_code='2087' AND enable=1 AND del_flag=0")
                dictionary = list(cur.fetchall())
                units = [dict(r, unit_dictionary=dictionary) for r in units]
            from .hangar_trend import read_snapshot
            from .indicator_catalog import normalize
            trend = read_snapshot(cur, request, self.cfg.max_rows,
                                  [normalize(r['index_name']) for r in units])
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise ReportError("RELATION_CARDINALITY_ERROR", "采样主键在关联后重复，拒绝放大统计")
        from .payloads import select_atmospheric_samples
        selected, payload_exclusions, selected_payloads = select_atmospheric_samples(
            rows, bindings, payloads, self.cfg.atmospheric_load_types)
        current = [r for r in rows if r["time"].date() >= request.start_date]
        provenance = {
            "timezone": "Asia/Shanghai", "as_of": request.as_of.isoformat(),
            "time_field": "bo_task_air.time", "time_storage_basis": "existing_service_local_datetime_contract",
            "sources": ["bo_uav_hangar", "bo_uav_register_info", "bo_uav_site", "bo_task_air"],
            "auxiliary_sources": ["bo_task", "bo_air_quality_index_project", "bo_load_register_info", "sys_dict_item"],
            "association_basis": "task.site_id with current site.hangar_id and site.uav_id; historical bindings unverified",
            "payloads": selected_payloads, "payload_exclusions": payload_exclusions,
            "registered_payloads": [{k: p.get(k) for k in
                ('id', 'uav_id', 'load_type', 'load_brand_code', 'load_model', 'state')}
                for p in payloads if p['load_type'] in self.cfg.atmospheric_load_types],
            "payload_selected_rows": len(selected),
            "bindings": bindings, "raw_current_rows": len(current), "raw_query_rows": len(rows),
            "test_fixture_rows": sum(str(r.get('task_id', '')).startswith('rptfx_') for r in selected),
            "test_fixture_task_ids": sorted({r['task_id'] for r in selected if str(r.get('task_id', '')).startswith('rptfx_')}),
            "first_sample": min((r["time"] for r in current), default=None),
            "latest_sample": max((r["time"] for r in current), default=None),
            "source_record_sha256": sorted(hashlib.sha256(str(i).encode()).hexdigest() for i in ids),
        }
        for key in ("first_sample", "latest_sample"):
            if provenance[key] is not None:
                provenance[key] = provenance[key].isoformat()
        return Dataset(selected, hangars, list(units), exclusions, provenance=provenance, trend=trend)

    def audit(self) -> dict:
        """Opt-in live probe: aggregate metadata only, no sample/coordinate persistence."""
        with self.session() as cur:
            cur.execute("SELECT COUNT(*) AS rows_total,MIN(time) AS first_sample,MAX(time) AS last_sample FROM bo_task_air WHERE del_flag=0")
            result = cur.fetchone()
            cur.execute("""SELECT COUNT(*) AS samples,
                SUM(t.id IS NULL) AS missing_task, SUM(s.id IS NULL) AS missing_current_hangar
                FROM bo_task_air a LEFT JOIN bo_task t ON t.id=a.task_id
                LEFT JOIN bo_uav_site s ON s.id=t.site_id WHERE a.del_flag=0""")
            result.update(cur.fetchone())
        return {k: v.isoformat() if hasattr(v, "isoformat") else int(v) if v is not None else None for k, v in result.items()}
