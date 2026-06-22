"""Compare static base fee vs Oscillon dynamic fee; LVR proxy."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from .oscillon_fee import BASE_FEE_BPS, FeeContext, FeeModel, fee_bps, select_fee_pips
from .scenario import is_drain_swap
from .volume_model import retained_volume_fraction

FeeMode = Literal["static", "dynamic"]


def toxic_usd_for_minute(drain_volume_usd, fee_bps, dev_bps):
    return max(0.0, drain_volume_usd * (dev_bps - fee_bps) / 10_000.0)


def run_minute(
    row: pd.Series,
    mode: FeeMode,
    *,
    static_fee_bps: float = BASE_FEE_BPS,
    k: int = 45,
    fee_model: FeeModel = "hybrid",
    competitor_fee_bps: float = 1.0,
    volume_eta: float = 2.0,
    pool_liquidity: int = 10**18,
) -> tuple[float, float]:
    """Returns (net_lvr_usd, fee_usd) for this minute."""
    oracle_depeg = int(row.get("oracle_depeg_bps", 0))
    chainlink = float(row.get("chainlink_usd", 1.0))
    net_amount0 = float(row.get("netAmount0", 0.0))
    is_drain = is_drain_swap(chainlink, net_amount0) and oracle_depeg >= 3

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

    drain_vol = float(row.get("drain_volume_usd", 0))
    retain = retained_volume_fraction(f_bps, competitor_fee_bps, eta=volume_eta)
    eff_drain = drain_vol * retain
    total_vol = float(row.get("volume_usd", 0)) * retain

    dev_for_lvr = float(oracle_depeg if is_drain else 0)
    toxic = toxic_usd_for_minute(eff_drain, f_bps, dev_for_lvr)
    fee_usd = total_vol * (f_bps / 10_000.0)
    return toxic, fee_usd


def run_full_comparison(
    df: pd.DataFrame,
    *,
    k: int = 45,
    fee_model: FeeModel = "hybrid",
    static_fee_bps: float = BASE_FEE_BPS,
) -> tuple[pd.DataFrame, dict]:
    total_static_loss = 0.0
    total_dynamic_loss = 0.0
    total_fees_static = 0.0
    total_fees_dynamic = 0.0

    for _, row in df.iterrows():
        net_s, fee_s = run_minute(row, "static", static_fee_bps=static_fee_bps, k=k)
        net_d, fee_d = run_minute(
            row, "dynamic", static_fee_bps=static_fee_bps, k=k, fee_model=fee_model
        )
        total_static_loss += net_s
        total_dynamic_loss += net_d
        total_fees_static += fee_s
        total_fees_dynamic += fee_d

    summary = {
        "lp_net_loss_static_usd": total_static_loss,
        "lp_net_loss_dynamic_usd": total_dynamic_loss,
        "lp_loss_reduced_usd": total_static_loss - total_dynamic_loss,
        "lp_loss_reduction_pct": (
            (1 - total_dynamic_loss / total_static_loss) * 100
            if total_static_loss > 0
            else 0.0
        ),
        "fees_collected_static_usd": total_fees_static,
        "fees_collected_dynamic_usd": total_fees_dynamic,
        "extra_fees_dynamic_usd": total_fees_dynamic - total_fees_static,
    }
    return pd.DataFrame(), summary
