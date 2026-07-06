"""
Stable pool swap direction and Oscillon drain classification.

token0 leg (default, matches on-chain USDC/USDT hook):
  Drain = token0 oracle below $1 AND token0 sold into pool (netAmount0 > 0).

token1 leg (USDT oracle backtest — separate feed, no mixing):
  Drain = token1 oracle below $1 AND token1 sold into pool (netAmount1 > 0).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from .pool_config import USDC_USDT, PoolConfig

OracleLeg = Literal["token0", "token1"]
ReferenceMode = Literal["dollar_peg", "nav"]

ETH_USDC_USDT_POOL = USDC_USDT.address
TOKEN0_SYMBOL = USDC_USDT.token0_symbol
TOKEN1_SYMBOL = USDC_USDT.token1_symbol
TOKEN0_DECIMALS = USDC_USDT.token0_decimals
TOKEN1_DECIMALS = USDC_USDT.token1_decimals

SwapDirection = Literal["sell_token0", "buy_token0", "flat", "sell_usdc", "buy_usdc"]


def is_peg_below(oracle_usd: float) -> bool:
    return oracle_usd < 1.0


def is_token0_sold_into_pool(net_amount0: float) -> bool:
    return net_amount0 > 0


def is_usdc_sold_into_pool(net_amount0: float) -> bool:
    return is_token0_sold_into_pool(net_amount0)


def is_usdt_sold_into_pool(net_amount1: float) -> bool:
    return net_amount1 > 0


def swap_direction(net_amount0: float, pool: PoolConfig = USDC_USDT) -> str:
    if net_amount0 > 0:
        if pool.token0_symbol == "USDC":
            return "sell_usdc"
        return f"sell_{pool.token0_symbol.lower()}"
    if net_amount0 < 0:
        if pool.token0_symbol == "USDC":
            return "buy_usdc"
        return f"buy_{pool.token0_symbol.lower()}"
    return "flat"


def is_drain_swap(
    oracle_usd: float,
    net_amount0: float,
    *,
    pool: PoolConfig = USDC_USDT,
    pool_price: float | None = None,
    reference_mode: ReferenceMode = "dollar_peg",
) -> bool:
    """
    True when cheap token0 is sold into the pool.

    dollar_peg: oracle (USD) < $1 and token0 sold in.
    nav: pool_price < NAV reference and token0 sold in (RWA / accumulating assets).
    """
    _ = pool
    if not is_token0_sold_into_pool(net_amount0):
        return False
    if reference_mode == "nav":
        if pool_price is None:
            return False
        return float(pool_price) < float(oracle_usd)
    return is_peg_below(oracle_usd)


def is_drain_swap_token1(
    oracle_usd: float,
    net_amount1: float,
    *,
    pool: PoolConfig = USDC_USDT,
) -> bool:
    """True when cheap token1 is sold into the pool during a below-peg token1 oracle."""
    _ = pool
    return is_peg_below(oracle_usd) and is_usdt_sold_into_pool(net_amount1)


def _token0_scale(pool: PoolConfig) -> float:
    return 10.0 ** pool.token0_decimals


def minute_volume_usd(
    in_amount0: float,
    in_amount1: float,
    *,
    pool: PoolConfig = USDC_USDT,
) -> float:
    scale0 = _token0_scale(pool)
    scale1 = 10.0 ** pool.token1_decimals
    return (abs(in_amount0) / scale0) + (abs(in_amount1) / scale1)


def drain_volume_usd(
    drain_size_usd: float,
    oracle_usd: float,
    net_amount0: float,
    *,
    pool: PoolConfig = USDC_USDT,
) -> float:
    return drain_size_usd if is_drain_swap(oracle_usd, net_amount0, pool=pool) else 0.0


def _token1_scale(pool: PoolConfig) -> float:
    return 10.0 ** pool.token1_decimals


def classify_prepared_swaps(
    df: pd.DataFrame,
    *,
    pool: PoolConfig = USDC_USDT,
    oracle_leg: OracleLeg = "token0",
    reference_mode: ReferenceMode = "dollar_peg",
) -> pd.DataFrame:
    """
    Add or refresh direction + drain columns on a minute-level swap DataFrame.
    Expects oracle_price from the leg-specific Chainlink feed (never mixed).
    """
    out = df.copy()
    scale0 = _token0_scale(pool)
    scale1 = _token1_scale(pool)
    leg = oracle_leg
    if "oracle_leg" in out.columns and len(out):
        leg = str(out["oracle_leg"].iloc[0])

    for col in ("netAmount0", "netAmount1", "inAmount0", "inAmount1"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    out["token0"] = pool.token0_symbol
    out["token1"] = pool.token1_symbol
    out["pool_address"] = pool.address
    out["oracle_leg"] = leg
    out["oracle_asset"] = pool.token1_symbol if leg == "token1" else pool.token0_symbol

    if "oracle_price" not in out.columns and "usdc_price" in out.columns and leg == "token0":
        out["oracle_price"] = out["usdc_price"]
    if "oracle_price" not in out.columns and "usdt_price" in out.columns and leg == "token1":
        out["oracle_price"] = out["usdt_price"]
    if "oracle_price" not in out.columns and "oracle_usd" in out.columns:
        out["oracle_price"] = out["oracle_usd"]

    if "netAmount0" in out.columns:
        out["token0_sold_into_pool"] = out["netAmount0"] > 0
        out["usdc_sold_into_pool"] = out["token0_sold_into_pool"]
        out["swap_direction"] = out["netAmount0"].apply(lambda x: swap_direction(x, pool))
    else:
        out["token0_sold_into_pool"] = False
        out["usdc_sold_into_pool"] = False
        out["swap_direction"] = "flat"

    if "netAmount1" in out.columns:
        out["token1_sold_into_pool"] = out["netAmount1"] > 0
        out["usdt_sold_into_pool"] = out["token1_sold_into_pool"]
    else:
        out["token1_sold_into_pool"] = False
        out["usdt_sold_into_pool"] = False

    out["reference_mode"] = reference_mode
    if "oracle_price" in out.columns:
        if reference_mode == "nav" and "pool_price" in out.columns:
            out["peg_below"] = out["pool_price"] < out["oracle_price"]
        else:
            out["peg_below"] = out["oracle_price"] < 1.0
        if leg == "token1" and "netAmount1" in out.columns:
            out["is_drain"] = out.apply(
                lambda r: is_drain_swap_token1(
                    float(r["oracle_price"]),
                    float(r["netAmount1"]),
                    pool=pool,
                ),
                axis=1,
            )
        elif "netAmount0" in out.columns:
            out["is_drain"] = out.apply(
                lambda r: is_drain_swap(
                    float(r["oracle_price"]),
                    float(r["netAmount0"]),
                    pool=pool,
                    pool_price=float(r["pool_price"]) if "pool_price" in r.index else None,
                    reference_mode=reference_mode,
                ),
                axis=1,
            )
        else:
            out["is_drain"] = out["peg_below"]
    elif "is_drain" not in out.columns:
        out["peg_below"] = False
        out["is_drain"] = False

    if leg == "token1" and "netAmount1" in out.columns:
        out["drain_size_usd"] = out["netAmount1"].clip(lower=0) / scale1
        out["restore_size_usd"] = (-out["netAmount1"]).clip(lower=0) / scale1
        out["swap_size_usd"] = out["drain_size_usd"] + out["restore_size_usd"]
    elif "netAmount0" in out.columns:
        out["drain_size_usd"] = out["netAmount0"].clip(lower=0) / scale0
        out["restore_size_usd"] = (-out["netAmount0"]).clip(lower=0) / scale0
        out["swap_size_usd"] = out["drain_size_usd"] + out["restore_size_usd"]
    elif "swap_size_usd" not in out.columns or out["swap_size_usd"].isna().any():
        if {"inAmount0", "inAmount1"}.issubset(out.columns):
            out["drain_size_usd"] = out["inAmount0"].abs() / scale0
            out["restore_size_usd"] = out["inAmount1"].abs() / scale1
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


def validate_prepared_swaps(
    df: pd.DataFrame,
    *,
    pool: PoolConfig | None = None,
) -> tuple[pd.DataFrame, int]:
    """
    Recompute is_drain from oracle + netAmount0; return fixed frame and mismatch count.
    """
    if pool is None and "pool_address" in df.columns:
        from .pool_config import POOL_PRESETS

        addr = str(df["pool_address"].iloc[0]).lower()
        for p in POOL_PRESETS.values():
            if p.address.lower() == addr:
                pool = p
                break
    if pool is None:
        pool = USDC_USDT

    if "oracle_price" not in df.columns:
        return df, 0
    leg: OracleLeg = "token1" if str(df.get("oracle_leg", pd.Series(["token0"])).iloc[0]) == "token1" else "token0"
    if leg == "token1" and "netAmount1" not in df.columns:
        return df, 0
    if leg == "token0" and "netAmount0" not in df.columns:
        return df, 0

    fixed = classify_prepared_swaps(df, pool=pool, oracle_leg=leg)
    if "is_drain" not in df.columns:
        return fixed, 0

    old = df["is_drain"].astype(bool)
    new = fixed["is_drain"].astype(bool)
    mismatches = int((old != new).sum())
    return fixed, mismatches
