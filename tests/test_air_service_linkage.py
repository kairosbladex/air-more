from datetime import datetime

import pymysql
import pytest

from airmore.service.config import Settings
from airmore.service.models import ReportRequest, ReportError, Dataset, TZ
from airmore.service.repository import MySQLRepository
from airmore.service.statistics import build_statistics
from airmore.service.writer import TemplateAnalysisWriter
from tests.test_air_service_worker import Cursor, Connection


def request():
    return ReportRequest(project_id='p', hangar_ids=['s'], requester_id='reader',
                         start_date='2026-09-16', end_date='2026-09-16', report_type='DAY',
                         as_of=datetime(2026, 9, 16, 12, tzinfo=TZ))


def binding(**changes):
    return dict(id='s', name='站点', physical_hangar_id='h', physical_hangar_name='机库',
                uav_id='u', hangar_deleted=0, uav_deleted=0, hangar_project='p',
                uav_project='p', enabled=1, data_valid=None, auth_status='', **changes)


def sample(identifier='a', hour=8, **changes):
    return dict(id=identifier, project_id='p', hangar_id='s', task_id='t', load_sn='device',
                load_type='sensor', time=datetime(2026, 9, 16, hour), lon=None, lat=None,
                alt=None, so2=0, no2=1, co=1, o3=1, pm10=1, pm2_5=1) | changes


def repository(replies):
    # Four platform reads follow the legacy raw/units snapshot for a DAY report.
    c = Connection(Cursor(replies + [[], [], [], []]))
    return MySQLRepository(Settings(atmospheric_load_types=("sensor",)), connect=lambda **kw: c), c


def test_current_binding_and_source_provenance():
    repo, c = repository([[binding()], [], [sample()], [dict(id='l',uav_id='u',load_imei='device',load_type='sensor')], []])
    data = repo.load(request())
    assert data.provenance['raw_current_rows'] == 1
    assert data.provenance['bindings'][0]['data_valid'] is None
    assert len(data.provenance['source_record_sha256'][0]) == 64
    sql = '\n'.join(s for s, _ in c.cur.sql)
    assert 'h.id=s.hangar_id' in sql and 'u.id=s.uav_id' in sql
    assert 'u.data_valid' not in sql and 'u.auth_status' not in sql
    assert c.rolled_back and c.closed


def test_legacy_blank_uav_project_requires_exclusive_active_site_ownership():
    legacy = binding() | {'uav_project':''}
    repo, _ = repository([[legacy], [{'project_id':'p'}], [], [], [], []])
    data = repo.load(request())
    assert data.provenance['bindings'][0]['uav_project'] == ''
    assert data.provenance['bindings'][0]['uav_project_basis'] == 'exclusive_active_site_project'
    for owners in ([], [{'project_id':None}], [{'project_id':'other'}],
                   [{'project_id':'p'},{'project_id':'other'}]):
        repo, _ = repository([[legacy.copy()], owners])
        with pytest.raises(ReportError) as error:
            repo.load(request())
        assert error.value.code == 'INVALID_DEVICE_LINKAGE'


@pytest.mark.parametrize('changes', [{'uav_id':None}, {'physical_hangar_id':None},
    {'uav_project':'other'}, {'hangar_project':'other'}, {'uav_deleted':1}, {'hangar_deleted':1}])
def test_invalid_links_fail_closed(changes):
    repo, c = repository([[binding() | changes]])
    with pytest.raises(ReportError, match='站点关联') as error:
        repo.load(request())
    assert error.value.code == 'INVALID_DEVICE_LINKAGE' and c.rolled_back


@pytest.mark.parametrize('replies', [[[binding(), binding()]], [[binding()], [], [sample(), sample()], [], []]])
def test_join_multiplication_is_not_silently_deduplicated(replies):
    repo, _ = repository(replies)
    with pytest.raises(ReportError) as error:
        repo.load(request())
    assert error.value.code == 'RELATION_CARDINALITY_ERROR'


@pytest.mark.parametrize('rows,code', [([], 'HANGAR_NOT_FOUND'),
    ([{'id':'h','project_id':'p'}, {'id':'j','project_id':'q'}], 'AMBIGUOUS_HANGAR')])
def test_name_lookup_never_picks_first(rows, code):
    repo, _ = repository([rows])
    with pytest.raises(ReportError) as error:
        repo.find_hangar_sites('同名机库')
    assert error.value.code == code


def test_one_hangar_multiple_sites():
    repo, _ = repository([[{'id':'h','project_id':'p'}], [{'id':'b'}, {'id':'a'}]])
    assert repo.find_hangar_sites('机库', 'p') == ['a','b']


@pytest.mark.parametrize('errno,code', [(1142,'DATABASE_PERMISSION_DENIED'), (1045,'DATABASE_PERMISSION_DENIED'), (2003,'DATABASE_ERROR')])
def test_database_error_categories(errno, code):
    def fail(**kw):
        raise pymysql.OperationalError(errno, 'secret')
    with pytest.raises(ReportError) as e:
        MySQLRepository(Settings(), connect=fail).load(request())
    assert e.value.code == code and 'secret' not in str(e.value)


def test_cutoff_timezone_boundary_duplicates_multiple_devices_and_idempotence():
    req = request()
    data = Dataset([sample(), sample('b'), sample('c', load_sn='other'),
                    sample('d', 12), sample('e', 0),
                    sample('f', time=datetime.fromisoformat('2026-09-15T23:59:00+00:00')),
                    sample('g', time=datetime(2026,9,17)),
                    sample('x', project_id='other')], {'s':'站点'})
    a = build_statistics(req, data)
    assert a == build_statistics(req, data)
    assert a['hangars']['s']['current']['samples'] == 4
    assert a['quality']['duplicate_rows'] == 1
    assert a['quality']['outside_query_window'] == 2
    assert a['quality']['out_of_scope'] == 1
    assert req.query_bounds()[1] == datetime(2026,9,16,12)
    text = TemplateAnalysisWriter().write(a['hangars']['s'])
    assert '共4条' in text.sections['overview'] and text.metadata['calls'] == 0


def test_cutoff_requires_timezone_and_empty_data_is_distinct():
    with pytest.raises(ValueError):
        ReportRequest(**(request().model_dump() | {'as_of':datetime(2026,9,16)}))
    repo, _ = repository([[binding()], [], [], [], []])
    assert repo.load(request()).samples == []
