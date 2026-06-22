"""
Fake depeg + oracle vs pool gap (arb bot view).

Oscillon uses Chainlink for *fee*. Arb bots compare *true* price (CEX / oracle)
to *pool* price. When pool lags, gap_bps ≈ extra extractable value per dollar of drain.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .swap_direction import is_drain_swap as classify_drain_swap
from .uniswap_math import price_to_tick, tick_to_price


@dataclass
class DepegScenario:
    """
    oracle_usd: Chainlink USDC/USD (e.g. 0.997 → 30 bps depeg)
    pool_lag_bps: pool slower than oracle (arb gap ≈ oracle_depeg - pool_depeg)
    duration_minutes: how long stress lasts
    volume_multiplier: scale swap flow during event
  """
    oracle_usd: float = 0.997
    pool_lag_bps: int = 15
    duration_minutes: int = 120
    volume_multiplier: float = 3.0
    start_offset_minutes: int = 60


def usd_to_depeg_bps(usd_price: float) -> int:
    return int(abs(1.0 - usd_price) * 10_000)


def enrich_oracle_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Add chainlink_price, pool_price, oracle_depeg_bps, pool_depeg_bps, arb_gap_bps."""
    out = df.copy()
    out["pool_price"] = out["price"]
    out["pool_depeg_bps"] = (abs(1.0 - out["pool_price"]) * 10_000).astype(int)

    if "chainlink_usd" not in out.columns:
        out["chainlink_usd"] = 1.0
    out["oracle_depeg_bps"] = (abs(1.0 - out["chainlink_usd"]) * 10_000).astype(int)
    out["arb_gap_bps"] = np.maximum(
        0, out["oracle_depeg_bps"] - out["pool_depeg_bps"]
    ).astype(int)
    return out


def inject_fake_depeg(
    df: pd.DataFrame,
    scenario: DepegScenario,
) -> pd.DataFrame:
    """
    Overlay a stress window on Demeter minute data.

    - Chainlink drops to oracle_usd for `duration_minutes`
    - Pool price lags (stays closer to $1 by pool_lag_bps)
    - Drain volume scales up (arb + panic flow)
    """
    out = enrich_oracle_pool(df.copy())
    for col in ("volume_usd", "drain_volume_usd", "netAmount0"):
        if col in out.columns:
            out[col] = out[col].astype(np.float64)
    n = len(out)
    if n == 0:
        return out

    start = min(scenario.start_offset_minutes, n - 1)
    end = min(start + scenario.duration_minutes, n)
    sl = slice(start, end)

    oracle_depeg = usd_to_depeg_bps(scenario.oracle_usd)
    pool_depeg = max(0, oracle_depeg - scenario.pool_lag_bps)
    pool_price = 1.0 - pool_depeg / 10_000.0

    out.iloc[sl, out.columns.get_loc("chainlink_usd")] = scenario.oracle_usd
    out.iloc[sl, out.columns.get_loc("oracle_depeg_bps")] = oracle_depeg
    out.iloc[sl, out.columns.get_loc("pool_depeg_bps")] = pool_depeg
    out.iloc[sl, out.columns.get_loc("pool_price")] = pool_price
    out.iloc[sl, out.columns.get_loc("price")] = pool_price
    out.iloc[sl, out.columns.get_loc("closeTick")] = price_to_tick(pool_price)
    out.iloc[sl, out.columns.get_loc("arb_gap_bps")] = scenario.pool_lag_bps

    # Arb sells depegging USDC into pool → token0 net inflow
    vol = out.loc[out.index[sl], "volume_usd"] * scenario.volume_multiplier
    out.loc[out.index[sl], "volume_usd"] = vol
    out.loc[out.index[sl], "drain_volume_usd"] = vol * 0.85
    out.loc[out.index[sl], "netAmount0"] = (
        out.loc[out.index[sl], "drain_volume_usd"] * 1e6
    )

    return out


def is_drain_swap(oracle_usd: float, net_amount0: float = 0.0) -> bool:
    """
    Drain = USDC below peg AND USDC sold into pool (token0 net inflow).

    When net_amount0 is 0 (e.g. stress injection overwrites flow), falls back
    to peg_below only so injected scenarios still activate dynamic fees.
    """
    if net_amount0 != 0.0:
        return classify_drain_swap(oracle_usd, net_amount0)
    return oracle_usd < 1.0
