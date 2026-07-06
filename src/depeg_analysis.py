"""Compare static base fee vs Oscillon dynamic fee; LVR proxy."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from .oscillon_fee import BASE_FEE_BPS, SMALL_DEPEG_BPS, FeeContext, FeeModel, fee_bps, select_fee_pips
from .scenario import is_drain_swap

FeeMode = Literal["static", "dynamic"]

VOLUME_LOSS_DEV_CAP_BPS = 50.0


def toxic_usd_for_minute(drain_volume_usd: float, fee_bps_val: float, dev_bps: float) -> float:
    return max(0.0, drain_volume_usd * (dev_bps - fee_bps_val) / 10_000.0)


def run_minute(
    row: pd.Series,
    mode: FeeMode,
    *,
    static_fee_bps: float = BASE_FEE_BPS,
    k: int = 45,
    fee_model: FeeModel = "hybrid",
    competitor_fee_bps: float = 4.0,
    volume_eta: float = 2.0,
    pool_liquidity: int = 10**18,
) -> tuple[float, float]:
    """Returns (lvr_usd, fee_usd) for this minute — matches backtest_mainnet economics."""
    _ = competitor_fee_bps, volume_eta
    oracle_depeg = int(row.get("oracle_depeg_bps", row.get("dev_bps", 0)))
    chainlink = float(row.get("chainlink_usd", row.get("oracle_price", 1.0)))
    net_amount0 = float(row.get("netAmount0", 0.0))
    is_drain = is_drain_swap(chainlink, net_amount0) and oracle_depeg >= SMALL_DEPEG_BPS

    if mode == "static":
        fee_pips = int(static_fee_bps * 100)
    else:
        fee_pips = select_fee_pips(
            FeeContext(
                depeg_bps=oracle_depeg,
                is_drain=is_drain,
                pool_liquidity=pool_liquidity,
                k_override=k,
                fee_model=fee_model,
            )
        )
    f_bps = fee_bps(fee_pips)

    drain_size = float(row.get("drain_size_usd", row.get("drain_volume_usd", 0)))
    restore_size = float(row.get("restore_size_usd", 0))
    total_size = drain_size + restore_size

    dev_for_lvr = float(oracle_depeg if is_drain else 0)
    lvr = toxic_usd_for_minute(drain_size, f_bps, dev_for_lvr)

    if is_drain:
        fee_usd = drain_size * f_bps / 10_000 + restore_size * BASE_FEE_BPS / 10_000
    else:
        fee_usd = total_size * f_bps / 10_000

    return lvr, fee_usd


def run_full_comparison(
    df: pd.DataFrame,
    *,
    k: int = 45,
    fee_model: FeeModel = "hybrid",
    static_fee_bps: float = BASE_FEE_BPS,
) -> tuple[pd.DataFrame, dict]:
    total_static_lvr = 0.0
    total_dynamic_lvr = 0.0
    total_fees_static = 0.0
    total_fees_dynamic = 0.0

    for _, row in df.iterrows():
        lvr_s, fee_s = run_minute(row, "static", static_fee_bps=static_fee_bps, k=k)
        lvr_d, fee_d = run_minute(
            row, "dynamic", static_fee_bps=static_fee_bps, k=k, fee_model=fee_model
        )
        total_static_lvr += lvr_s
        total_dynamic_lvr += lvr_d
        total_fees_static += fee_s
        total_fees_dynamic += fee_d

    summary = {
        "lvr_static_usd": total_static_lvr,
        "lvr_dynamic_usd": total_dynamic_lvr,
        "lvr_reduced_usd": total_static_lvr - total_dynamic_lvr,
        "lvr_reduction_pct": (
            (1 - total_dynamic_lvr / total_static_lvr) * 100
            if total_static_lvr > 0
            else 0.0
        ),
        "fees_collected_static_usd": total_fees_static,
        "fees_collected_dynamic_usd": total_fees_dynamic,
        "extra_fees_dynamic_usd": total_fees_dynamic - total_fees_static,
        # Legacy keys for scripts/calc_apr.py
        "lp_net_loss_static_usd": total_static_lvr,
        "lp_net_loss_dynamic_usd": total_dynamic_lvr,
        "lp_loss_reduced_usd": total_static_lvr - total_dynamic_lvr,
        "lp_loss_reduction_pct": (
            (1 - total_dynamic_lvr / total_static_lvr) * 100
            if total_static_lvr > 0
            else 0.0
        ),
    }
    return pd.DataFrame(), summary
