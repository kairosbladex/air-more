"""单位换算测试：GB 3095-2012 参比状态 298.15 K / 101.325 kPa。"""

import pytest

from airmore.domain.units import (MOLAR_VOLUME_L, ppm_to_mgm3, ppm_to_ugm3,
                                  ugm3_to_ppm)


def test_molar_volume():
    """参比状态摩尔体积约 24.465 L/mol。"""
    assert MOLAR_VOLUME_L == pytest.approx(24.465, abs=0.01)


@pytest.mark.parametrize("mw,ppm,expected_ugm3", [
    (64.06, 1.0, 2618.4),   # SO2：1 ppm ≈ 2618 μg/m³
    (46.01, 1.0, 1880.6),   # NO2
    (48.00, 1.0, 1962.0),   # O3
    (28.01, 1.0, 1144.9),   # CO
    (16.04, 2.0, 1311.1),   # CH4：2 ppm ≈ 1311 μg/m³
])
def test_ppm_to_ugm3(mw, ppm, expected_ugm3):
    assert ppm_to_ugm3(ppm, mw) == pytest.approx(expected_ugm3, rel=0.001)


def test_roundtrip():
    for mw in (64.06, 46.01, 48.00, 28.01, 16.04):
        assert ugm3_to_ppm(ppm_to_ugm3(3.7, mw), mw) == pytest.approx(3.7)


def test_ppm_to_mgm3():
    assert ppm_to_mgm3(1.0, 28.01) == pytest.approx(1.1449, rel=0.001)


def test_invalid_molecular_weight():
    with pytest.raises(ValueError):
        ppm_to_ugm3(1.0, 0)
    with pytest.raises(ValueError):
        ugm3_to_ppm(100.0, -1)
