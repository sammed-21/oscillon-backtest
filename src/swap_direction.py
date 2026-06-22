"""
USDC/USDT pool swap direction and Oscillon drain classification.

Ethereum Uniswap v3 pool 0x3416cf6c708da44db2624d63ea0aaef7113527c6:
  token0 = USDC (6 decimals) — lower contract address
  token1 = USDT (6 decimals)

Uniswap Swap event sign convention (per swap, aggregated per minute in CSV):
  netAmount0 > 0  → USDC flowed into pool (trader sold USDC)
  netAmount0 < 0  → USDC flowed out of pool (trader bought USDC)
  inAmount0       → total USDC sent into pool this minute (≥ 0)
  inAmount1       → total USDT sent into pool this minute (≥ 0)

Oscillon drain (matches on-chain hook intent):
  Chainlink USDC below $1 peg AND trader sells USDC into the pool.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

ETH_USDC_USDT_POOL = "0x3416cf6c708da44db2624d63ea0aaef7113527c6"
TOKEN0_SYMBOL = "USDC"
TOKEN1_SYMBOL = "USDT"
TOKEN0_DECIMALS = 6
TOKEN1_DECIMALS = 6

SwapDirection = Literal["sell_usdc", "buy_usdc", "flat"]


def is_peg_below(oracle_usd: float) -> bool:
    return oracle_usd < 1.0


def is_usdc_sold_into_pool(net_amount0: float) -> bool:
    return net_amount0 > 0


def is_usdt_sold_into_pool(net_amount1: float) -> bool:
    return net_amount1 > 0


def swap_direction(net_amount0: float) -> SwapDirection:
    if net_amount0 > 0:
        return "sell_usdc"
    if net_amount0 < 0:
        return "buy_usdc"
    return "flat"


def is_drain_swap(oracle_usd: float, net_amount0: float) -> bool:
    """True when cheap USDC is sold into the pool during a below-peg oracle."""
    return is_peg_below(oracle_usd) and is_usdc_sold_into_pool(net_amount0)


def minute_volume_usd(in_amount0: float, in_amount1: float) -> float:
    """Total notional swapped in a minute (both directions)."""
    return (abs(in_amount0) + abs(in_amount1)) / 1e6


def drain_volume_usd(volume_usd: float, oracle_usd: float, net_amount0: float) -> float:
    return volume_usd if is_drain_swap(oracle_usd, net_amount0) else 0.0


def classify_prepared_swaps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add or refresh direction + drain columns on a minute-level swap DataFrame.
    Expects netAmount0 and oracle_price (or usdc_price) when available.
    """
    out = df.copy()

    for col in ("netAmount0", "netAmount1", "inAmount0", "inAmount1"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    out["token0"] = TOKEN0_SYMBOL
    out["token1"] = TOKEN1_SYMBOL

    if "oracle_price" not in out.columns and "usdc_price" in out.columns:
        out["oracle_price"] = out["usdc_price"]

    if "netAmount0" in out.columns:
        out["usdc_sold_into_pool"] = out["netAmount0"] > 0
        out["swap_direction"] = out["netAmount0"].apply(swap_direction)
    else:
        out["usdc_sold_into_pool"] = False
        out["swap_direction"] = "flat"

    if "oracle_price" in out.columns:
        out["peg_below"] = out["oracle_price"] < 1.0
        if "netAmount0" in out.columns:
            out["is_drain"] = out.apply(
                lambda r: is_drain_swap(float(r["oracle_price"]), float(r["netAmount0"])),
                axis=1,
            )
        else:
            out["is_drain"] = out["peg_below"]
    elif "is_drain" not in out.columns:
        out["peg_below"] = False
        out["is_drain"] = False

    if "netAmount0" in out.columns:
        out["drain_size_usd"] = out["netAmount0"].clip(lower=0) / 1e6
        out["restore_size_usd"] = (-out["netAmount0"]).clip(lower=0) / 1e6
        out["swap_size_usd"] = out["drain_size_usd"] + out["restore_size_usd"]
    elif "swap_size_usd" not in out.columns or out["swap_size_usd"].isna().any():
        if {"inAmount0", "inAmount1"}.issubset(out.columns):
            out["drain_size_usd"] = out["inAmount0"].abs() / 1e6
            out["restore_size_usd"] = out["inAmount1"].abs() / 1e6
            out["swap_size_usd"] = out["drain_size_usd"] + out["restore_size_usd"]
        else:
            out["drain_size_usd"] = 0.0
            out["restore_size_usd"] = 0.0
            out["swap_size_usd"] = 0.0

    out["drain_volume_usd"] = np.where(
        out["is_drain"],
        out["drain_size_usd"],
        0.0,
    )
    return out


def validate_prepared_swaps(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Recompute is_drain from oracle + netAmount0; return fixed frame and mismatch count.
    """
    if "netAmount0" not in df.columns or "oracle_price" not in df.columns:
        return df, 0

    fixed = classify_prepared_swaps(df)
    if "is_drain" not in df.columns:
        return fixed, 0

    old = df["is_drain"].astype(bool)
    new = fixed["is_drain"].astype(bool)
    mismatches = int((old != new).sum())
    return fixed, mismatches
