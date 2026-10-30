"""ppm ↔ μg/m³ 单位换算。

按 GB 3095-2012 参比状态：298.15 K / 101.325 kPa。
该状态下气体摩尔体积 Vm = R·T/P = 8.314 × 298.15 / 101.325 ≈ 24.465 L/mol。

质量浓度与体积浓度关系：
    mg/m³  = ppm × MW / Vm        （MW 单位 g/mol，Vm 单位 L/mol）
    μg/m³  = ppm × MW × 101.325 × 1000 / (8.314 × 298.15)
           ≈ ppm × MW / 24.465 × 1000
"""

from __future__ import annotations

R = 8.314            # J/(mol·K)
T_REF = 298.15       # K，GB 3095-2012 参比状态温度
P_REF = 101.325      # kPa，GB 3095-2012 参比状态压力

# 参比状态摩尔体积（L/mol）
MOLAR_VOLUME_L = R * T_REF / P_REF  # ≈ 24.465


def ppm_to_ugm3(ppm: float, molecular_weight: float) -> float:
    """ppm → μg/m³（参比状态 298.15 K / 101.325 kPa）。"""
    if molecular_weight <= 0:
        raise ValueError("molecular_weight 必须为正")
    return ppm * molecular_weight * 1000.0 / MOLAR_VOLUME_L


def ugm3_to_ppm(ugm3: float, molecular_weight: float) -> float:
    """μg/m³ → ppm（参比状态 298.15 K / 101.325 kPa）。"""
    if molecular_weight <= 0:
        raise ValueError("molecular_weight 必须为正")
    return ugm3 * MOLAR_VOLUME_L / (molecular_weight * 1000.0)


def ppm_to_mgm3(ppm: float, molecular_weight: float) -> float:
    """ppm → mg/m³。"""
    return ppm_to_ugm3(ppm, molecular_weight) / 1000.0
