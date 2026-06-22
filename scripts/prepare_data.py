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

from src.swap_direction import (
    ETH_USDC_USDT_POOL,
    TOKEN0_SYMBOL,
    TOKEN1_SYMBOL,
    classify_prepared_swaps,
)

Q96 = 2**96


def sqrtx96_to_price(sqrt_price_x96: float, decimals0: int = 6, decimals1: int = 6) -> float:
    sqrt_price = float(sqrt_price_x96) / Q96
    raw_price = sqrt_price**2
    return raw_price * (10**decimals0) / (10**decimals1)


def main() -> None:
    p = argparse.ArgumentParser(description="Prepare swap + oracle merged dataset")
    p.add_argument(
        "--swaps",
        default="data/ethereum-0x3416cf6c708da44db2624d63ea0aaef7113527c6-2023-03-10.minute.csv",
        help="Single swap CSV path (tick-level) OR leave default and use --data-dir/--chain/--pool/--start/--end for minute files",
    )
    p.add_argument("--data-dir", default="data")
    p.add_argument("--chain", default="ethereum")
    p.add_argument("--pool", default=ETH_USDC_USDT_POOL)
    p.add_argument("--start", default="2023-03-10")
    p.add_argument("--end", default="2023-03-15")
    p.add_argument("--use-minute-files", action="store_true", help="Load daily *.minute.csv files for the date range")
    p.add_argument("--oracle", default="data/chainlink_usdc_2023.csv")
    p.add_argument("--out", default="data/prepared_swaps.csv")
    p.add_argument("--min-swap-usd", type=float, default=100.0)
    args = p.parse_args()

    if args.use_minute_files:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        if end < start:
            raise ValueError("--end must be on/after --start")
        frames: list[pd.DataFrame] = []
        day = start
        while day <= end:
            fp = Path(args.data_dir) / f"{args.chain.lower()}-{args.pool.lower()}-{day:%Y-%m-%d}.minute.csv"
            if not fp.exists():
                raise FileNotFoundError(f"Missing minute file: {fp}")
            frames.append(pd.read_csv(fp))
            day += timedelta(days=1)
        swaps = pd.concat(frames, ignore_index=True)
        swaps["timestamp"] = pd.to_datetime(swaps["timestamp"], utc=True).dt.tz_localize(None)
        if "pool_price" not in swaps.columns:
            swaps["pool_price"] = swaps.get("price", 1.0)
    else:
        swaps = pd.read_csv(args.swaps)
        swaps["timestamp"] = pd.to_datetime(swaps["block_timestamp"], utc=True).dt.tz_localize(None)
        swaps["pool_price"] = swaps["sqrt_price_x96"].apply(lambda x: sqrtx96_to_price(x, 6, 6))

    swaps = swaps.sort_values("timestamp")
    print(f"Pool: {args.pool} (token0={TOKEN0_SYMBOL}, token1={TOKEN1_SYMBOL})")
    print(f"Total swaps loaded: {len(swaps):,}")
    print(f"Date range: {swaps['timestamp'].min()} to {swaps['timestamp'].max()}")

    oracle = pd.read_csv(args.oracle)
    if "minute" not in oracle.columns and "call_block_time" in oracle.columns:
        oracle = oracle.rename(columns={"call_block_time": "minute"})
    if "usdc_price" not in oracle.columns and "price" in oracle.columns:
        oracle = oracle.rename(columns={"price": "usdc_price"})
    oracle["timestamp"] = pd.to_datetime(oracle["minute"], utc=True).dt.tz_localize(None)
    oracle = oracle.sort_values("timestamp")

    swaps = pd.merge_asof(
        swaps.sort_values("timestamp"),
        oracle[["timestamp", "usdc_price"]].sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    print(f"Swaps with oracle data: {swaps['usdc_price'].notna().sum():,}")

    swaps["oracle_price"] = swaps["usdc_price"]
    swaps["dev_bps"] = (swaps["oracle_price"] - 1.0).abs() * 10000
    missing_oracle = swaps["oracle_price"].isna().sum()
    if missing_oracle:
        print(f"Dropping {missing_oracle:,} swaps with no Chainlink match")
        swaps = swaps[swaps["oracle_price"].notna()].copy()

    swaps = classify_prepared_swaps(swaps)
    swaps = swaps[swaps["swap_size_usd"] > args.min_swap_usd]

    sell_usdc = (swaps["swap_direction"] == "sell_usdc").sum()
    buy_usdc = (swaps["swap_direction"] == "buy_usdc").sum()
    drain = swaps["is_drain"].sum()

    print("\nData preparation complete.")
    print(f"Total swaps: {len(swaps):,}")
    print(f"  sell_usdc minutes: {sell_usdc:,}")
    print(f"  buy_usdc minutes:  {buy_usdc:,}")
    print(f"  drain swaps (peg_below & USDC in): {drain:,} ({swaps['is_drain'].mean() * 100:.1f}%)")
    print(f"Avg dev_bps: {swaps['dev_bps'].mean():.2f}")
    print(f"Max dev_bps: {swaps['dev_bps'].max():.2f}")
    print(f"Total volume: ${swaps['swap_size_usd'].sum():,.0f}")
    print(f"Drain volume: ${swaps['drain_volume_usd'].sum():,.0f}")

    swaps.to_csv(args.out, index=False)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
