#!/usr/bin/env python3
"""Prepare ETH mainnet swaps with oracle deviations + drain classification."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pool_config import USDC_USDT, get_pool_config
from src.swap_direction import classify_prepared_swaps
from src.uniswap_math import tick_to_price

Q96 = 2**96


def sqrtx96_to_price(
    sqrt_price_x96: float,
    decimals0: int = 6,
    decimals1: int = 6,
) -> float:
    sqrt_price = float(sqrt_price_x96) / Q96
    raw_price = sqrt_price**2
    return raw_price * (10**decimals0) / (10**decimals1)


def tick_to_pool_price(tick: int, pool) -> float:
    """USDT per token0 from Uniswap tick."""
    return tick_to_price(int(tick)) * (10 ** pool.token0_decimals) / (10 ** pool.token1_decimals)


def main() -> None:
    p = argparse.ArgumentParser(description="Prepare swap + oracle merged dataset")
    p.add_argument(
        "--swaps",
        default="data/ethereum-0x3416cf6c708da44db2624d63ea0aaef7113527c6-2023-03-10.minute.csv",
        help="Single swap CSV path OR use --use-minute-files",
    )
    p.add_argument("--data-dir", default="data")
    p.add_argument("--chain", default="ethereum")
    p.add_argument("--pool-preset", default="usdc-usdt", help="usdc-usdt | usde-usdt | pyusd-usdc | fdusd-usdc-bsc")
    p.add_argument("--pool", default="", help="Override pool address")
    p.add_argument("--start", default="2023-03-10")
    p.add_argument("--end", default="2023-03-15")
    p.add_argument("--use-minute-files", action="store_true")
    p.add_argument("--oracle", default="")
    p.add_argument(
        "--oracle-source",
        choices=["chainlink", "pool", "nav"],
        default="chainlink",
        help="chainlink=merge CSV oracle; pool=on-chain tick; nav=published NAV reference (RWA)",
    )
    p.add_argument(
        "--nav-csv",
        default="",
        help="NAV time series CSV (minute/timestamp + nav_price) when --oracle-source nav",
    )
    p.add_argument(
        "--reference-mode",
        choices=["dollar_peg", "nav"],
        default="",
        help="Drain classification mode (default: nav when --oracle-source nav, else dollar_peg)",
    )
    p.add_argument(
        "--oracle-leg",
        choices=["token0", "token1"],
        default="token0",
        help="token0=USDC oracle+netAmount0 (on-chain hook); token1=USDT oracle+netAmount1",
    )
    p.add_argument("--out", default="data/prepared_swaps.csv")
    p.add_argument("--min-swap-usd", type=float, default=100.0)
    args = p.parse_args()

    pool_key = args.pool or args.pool_preset
    pool = get_pool_config(pool_key)

    if args.use_minute_files:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        if end < start:
            raise ValueError("--end must be on/after --start")
        frames: list[pd.DataFrame] = []
        day = start
        while day <= end:
            fp = (
                Path(args.data_dir)
                / f"{pool.chain.lower()}-{pool.address.lower()}-{day:%Y-%m-%d}.minute.csv"
            )
            if not fp.exists():
                raise FileNotFoundError(f"Missing minute file: {fp}")
            frames.append(pd.read_csv(fp))
            day += timedelta(days=1)
        swaps = pd.concat(frames, ignore_index=True)
        swaps["timestamp"] = pd.to_datetime(swaps["timestamp"], utc=True).dt.tz_localize(None)
        if "pool_price" not in swaps.columns:
            if "closeTick" in swaps.columns:
                swaps["pool_price"] = swaps["closeTick"].apply(
                    lambda t: tick_to_pool_price(t, pool)
                )
            else:
                swaps["pool_price"] = swaps.get("price", 1.0)
    else:
        swaps = pd.read_csv(args.swaps)
        swaps["timestamp"] = pd.to_datetime(swaps["block_timestamp"], utc=True).dt.tz_localize(None)
        swaps["pool_price"] = swaps["sqrt_price_x96"].apply(
            lambda x: sqrtx96_to_price(x, pool.token0_decimals, pool.token1_decimals)
        )

    swaps = swaps.sort_values("timestamp")
    print(f"Pool: {pool.address} (token0={pool.token0_symbol}, token1={pool.token1_symbol})")
    print(f"Total swaps loaded: {len(swaps):,}")
    print(f"Date range: {swaps['timestamp'].min()} to {swaps['timestamp'].max()}")

    oracle_leg = args.oracle_leg
    oracle_asset = pool.token1_symbol if oracle_leg == "token1" else pool.token0_symbol
    reference_mode = args.reference_mode or (
        "nav" if args.oracle_source == "nav" else "dollar_peg"
    )

    if args.oracle_source == "pool":
        swaps["oracle_price"] = swaps["pool_price"].astype(float)
        print("Oracle source: pool tick price (on-chain proxy)")
    elif args.oracle_source == "nav":
        nav_path = args.nav_csv or f"data/nav_{oracle_asset.lower()}_sample.csv"
        nav = pd.read_csv(nav_path)
        ts_col = "minute" if "minute" in nav.columns else "timestamp"
        if ts_col not in nav.columns:
            raise ValueError(f"NAV CSV needs minute or timestamp column: {nav_path}")
        price_col = None
        for candidate in ("nav_price", "nav_usd", "price", f"{oracle_asset.lower()}_nav"):
            if candidate in nav.columns:
                price_col = candidate
                break
        if price_col is None:
            raise ValueError(f"No nav_price column in {nav_path}")
        nav["timestamp"] = pd.to_datetime(nav[ts_col], utc=True).dt.tz_localize(None)
        nav = nav.sort_values("timestamp")
        swaps = pd.merge_asof(
            swaps.sort_values("timestamp"),
            nav[["timestamp", price_col]].rename(columns={price_col: "oracle_price"}),
            on="timestamp",
            direction="backward",
        )
        print(f"Oracle source: NAV reference ({nav_path})")
        print(f"Reference mode: {reference_mode}")
        print(f"Swaps with NAV data: {swaps['oracle_price'].notna().sum():,}")
        missing = swaps["oracle_price"].isna().sum()
        if missing:
            print(f"Dropping {missing:,} swaps with no NAV match")
            swaps = swaps[swaps["oracle_price"].notna()].copy()
    else:
        default_oracle = (
            f"data/chainlink_{oracle_asset.lower()}_{args.start[:4]}.csv"
        )
        oracle_path = args.oracle or default_oracle
        oracle = pd.read_csv(oracle_path)
        if "minute" not in oracle.columns and "call_block_time" in oracle.columns:
            oracle = oracle.rename(columns={"call_block_time": "minute"})
        price_col = None
        for candidate in (
            f"{oracle_asset.lower()}_price",
            f"{pool.token0_symbol.lower()}_price",
            "usdc_price",
            "usdt_price",
            "oracle_price",
            "price",
        ):
            if candidate in oracle.columns:
                price_col = candidate
                break
        if price_col is None:
            raise ValueError(f"No price column found in {oracle_path}")
        oracle["timestamp"] = pd.to_datetime(oracle["minute"], utc=True).dt.tz_localize(None)
        oracle = oracle.sort_values("timestamp")
        # Backward as-of on timestamp; no max gap (staleness can understate dev_bps — conservative).
        swaps = pd.merge_asof(
            swaps.sort_values("timestamp"),
            oracle[["timestamp", price_col]].rename(columns={price_col: "oracle_price"}),
            on="timestamp",
            direction="backward",
        )
        print(f"Oracle leg: {oracle_leg} ({oracle_asset})")
        print(f"Oracle source: {oracle_path} ({price_col})")
        print(f"Swaps with oracle data: {swaps['oracle_price'].notna().sum():,}")
        missing_oracle = swaps["oracle_price"].isna().sum()
        if missing_oracle:
            print(f"Dropping {missing_oracle:,} swaps with no oracle match")
            swaps = swaps[swaps["oracle_price"].notna()].copy()

    if reference_mode == "nav" and "pool_price" in swaps.columns:
        swaps["dev_bps"] = (
            (swaps["pool_price"] - swaps["oracle_price"]).abs()
            / swaps["oracle_price"].clip(lower=1e-12)
            * 10000
        )
    else:
        swaps["dev_bps"] = (swaps["oracle_price"] - 1.0).abs() * 10000
    swaps = classify_prepared_swaps(
        swaps, pool=pool, oracle_leg=oracle_leg, reference_mode=reference_mode
    )
    swaps = swaps[swaps["swap_size_usd"] > args.min_swap_usd]

    # Persist leg metadata in CSV for downstream backtests
    swaps["oracle_leg"] = oracle_leg
    swaps["oracle_asset"] = oracle_asset
    swaps["reference_mode"] = reference_mode

    drain = swaps["is_drain"].sum()
    sold_col = "netAmount1" if oracle_leg == "token1" else "netAmount0"
    print("\nData preparation complete.")
    print(f"Total swaps: {len(swaps):,}")
    print(
        f"  drain swaps (peg_below & {oracle_asset} in via {sold_col}): "
        f"{drain:,} ({swaps['is_drain'].mean() * 100:.1f}%)"
    )
    print(f"Avg dev_bps: {swaps['dev_bps'].mean():.2f}")
    print(f"Max dev_bps: {swaps['dev_bps'].max():.2f}")
    print(f"Total volume: ${swaps['swap_size_usd'].sum():,.0f}")
    print(f"Drain volume: ${swaps['drain_volume_usd'].sum():,.0f}")

    swaps.to_csv(args.out, index=False)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
