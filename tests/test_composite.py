"""综合指数与排名测试（演示口径：Σ 浓度÷GB 3095-2012 二级 24h 限值）。"""

import pytest

from airmore.domain.composite import (COMPOSITE_LIMITS, assign_ranks,
                                      composite_index, index_components,
                                      rank_color)


def _means(v=1.0):
    return {f: COMPOSITE_LIMITS[f] * v for f in COMPOSITE_LIMITS}


def test_components_at_limit():
    comps = index_components(_means(1.0))
    assert all(c == pytest.approx(1.0) for c in comps.values())
    assert composite_index(comps) == pytest.approx(6.0)


def test_components_half_limit():
    comps = index_components(_means(0.5))
    assert composite_index(comps) == pytest.approx(3.0)


def test_missing_pollutant_partial_sum():
    means = _means(1.0)
    means["pm2_5"] = None
    comps = index_components(means)
    assert comps["pm2_5"] is None
    assert composite_index(comps) == pytest.approx(5.0)


def test_all_missing_returns_none():
    comps = index_components({f: None for f in COMPOSITE_LIMITS})
    assert composite_index(comps) is None


def test_index_rounding_2_decimals():
    means = {f: 1.0 for f in COMPOSITE_LIMITS}  # 每项 1/限值，和为无理小数
    assert composite_index(index_components(means)) == round(
        sum(round(1.0 / COMPOSITE_LIMITS[f], 4) for f in COMPOSITE_LIMITS), 2)


def test_assign_ranks_ascending():
    ranks = assign_ranks({"A": 2.5, "B": 1.2, "C": 1.8, "D": 3.1})
    assert ranks == {"B": 1, "C": 2, "A": 3, "D": 4}


def test_assign_ranks_missing_at_bottom():
    ranks = assign_ranks({"A": 2.0, "B": None, "C": 1.0})
    assert ranks["C"] == 1
    assert ranks["A"] == 2
    assert ranks["B"] == 3  # 缺测仍占名次，排在最后


@pytest.mark.parametrize("rank,n,expected", [
    (1, 4, None), (2, 4, "yellow"), (3, 4, "yellow"), (4, 4, "red"),
    (7, 10, None), (8, 10, "yellow"), (9, 10, "yellow"), (10, 10, "red"),
    (1, 1, None), (1, 3, None), (3, 3, None),  # 排名对象 <4 不着色
])
def test_rank_color(rank, n, expected):
    assert rank_color(rank, n) == expected
