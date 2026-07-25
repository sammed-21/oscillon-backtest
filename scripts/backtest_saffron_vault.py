#!/usr/bin/env python3
"""
CLI to backtest LVR for a Saffron Finance fixed-term concentrated-liquidity
vault built on Uniswap V3.

How this differs from the Oscillon stablecoin backtest (scripts/backtest_mainnet.py):
  - Requires a tick range (--tick-lower / --tick-upper); LVR is gated to zero
    whenever the pool price traded outside that range for the vault's term.
  - Uses the general volatile-pair depeg formula against an external oracle
    price rather than assuming a $1.00 peg.
  - Reports vault-specific metrics: gross fee income, total LVR extracted,
    net yield (in USD and as an annualised APR against vault TVL), whether
    the fixed rate offered to LPs was priced accurately, LVR as a percent
    of gross fees, and time spent in range vs out of range.

Input: a prepared swap CSV (--prepared) with pool-price + fee-income columns,
and an oracle price CSV (--oracle) with a timestamp + price column for the
externally-referenced asset (e.g. NVDA, ETH). The two are merged on
timestamp and filtered to [--vault-start, --vault-end].
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.saffron_lvr import SaffronLVR


def load_and_merge(
    prepared_path: str,
    oracle_path: str,
    vault_start: str,
    vault_end: str,
    fee_tier_bps: float,
) -> pd.DataFrame:
    swaps = pd.read_csv(prepared_path, parse_dates=["timestamp"])

    if "oracle_price" in swaps.columns and not oracle_path:
        merged = swaps.copy()
    else:
        oracle = pd.read_csv(oracle_path, parse_dates=["timestamp"])
        merged = pd.merge_asof(
            swaps.sort_values("timestamp").drop(columns=["oracle_price"], errors="ignore"),
            oracle.sort_values("timestamp")[["timestamp", "price"]].rename(columns={"price": "oracle_price"}),
            on="timestamp",
            direction="nearest",
        )

    start = pd.Timestamp(vault_start)
    end = pd.Timestamp(vault_end)
    merged = merged[(merged["timestamp"] >= start) & (merged["timestamp"] <= end)].reset_index(drop=True)

    if "drain_volume_usd" not in merged.columns and "drain_size_usd" in merged.columns:
        merged["drain_volume_usd"] = merged["drain_size_usd"]
    if "fee_income_usd" not in merged.columns and "swap_size_usd" in merged.columns:
        # Fee tier applies to all pool volume; calculate_vault_summary gates it to
        # in-range swaps only, since out-of-range positions earn zero fees.
        merged["fee_income_usd"] = merged["swap_size_usd"] * fee_tier_bps / 10_000

    required = {"timestamp", "drain_volume_usd", "pool_price", "oracle_price", "is_drain", "fee_income_usd"}
    missing = required - set(merged.columns)
    if missing:
        raise ValueError(f"Merged data missing required columns: {sorted(missing)}")

    return merged


def format_report(summary: dict, args: argparse.Namespace) -> str:
    lines = [
        "# Saffron Vault LVR Backtest",
        "",
        f"- Tick range: {args.tick_lower} - {args.tick_upper}",
        f"- Fee tier: {args.fee_tier} bps",
        f"- Vault term: {args.vault_start} -> {args.vault_end}",
        f"- Vault TVL: ${args.tvl:,.2f}",
        f"- Fixed APR offered: {args.fixed_apr:.2f}%",
        "",
        "## Results",
        "",
        f"- Gross fee income (in-range only): ${summary['gross_fee_income_usd']:,.2f}",
        f"- Fee income missed while out of range: ${summary['fee_income_missed_out_of_range_usd']:,.2f}",
        f"- Total LVR extracted: ${summary['total_lvr_usd']:,.2f}",
        f"- Net yield: ${summary['net_yield_usd']:,.2f}",
        f"- Net yield APR: {summary['net_yield_apr']:.2f}%",
        f"- Fixed rate accuracy (pricing error): {summary['pricing_error_bps']:.1f} bps"
        f" ({'over' if summary['pricing_error_bps'] < 0 else 'under'}-priced vs realised net yield)",
        f"- LVR as % of gross fees: {summary['lvr_as_pct_of_gross_fees']:.1f}%",
        f"- Time in range: {summary['time_in_range_pct']:.1f}% "
        f"({summary['in_range_swaps']:,} in-range / {summary['out_of_range_swaps']:,} out-of-range swaps)",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest LVR for a Saffron Finance vault")
    p.add_argument("--prepared", required=True, help="Prepared swap CSV path")
    p.add_argument(
        "--oracle",
        default="",
        help="Oracle price CSV (timestamp,price columns). Omit if --prepared already has oracle_price.",
    )
    p.add_argument("--tick-lower", type=float, required=True, help="Lower price bound of vault range")
    p.add_argument("--tick-upper", type=float, required=True, help="Upper price bound of vault range")
    p.add_argument("--fee-tier", type=float, required=True, help="Pool fee tier in bps")
    p.add_argument("--fixed-apr", type=float, required=True, help="Fixed rate offered to LP, in percent")
    p.add_argument("--vault-start", required=True, help="Vault start datetime (ISO format)")
    p.add_argument("--vault-end", required=True, help="Vault end datetime (ISO format)")
    p.add_argument("--tvl", type=float, required=True, help="Vault TVL in USD")
    p.add_argument("--out", default="output/saffron_vault_backtest.md", help="Output report path")
    args = p.parse_args()

    start_dt = datetime.fromisoformat(args.vault_start)
    end_dt = datetime.fromisoformat(args.vault_end)
    duration_days = (end_dt - start_dt).total_seconds() / 86_400

    merged = load_and_merge(args.prepared, args.oracle, args.vault_start, args.vault_end, args.fee_tier)

    vault = SaffronLVR()
    summary = vault.calculate_vault_summary(
        merged,
        tick_lower_price=args.tick_lower,
        tick_upper_price=args.tick_upper,
        fee_tier_bps=args.fee_tier,
        fixed_apr=args.fixed_apr,
        vault_duration_days=duration_days,
        tvl_usd=args.tvl,
    )

    report = format_report(summary, args)
    print(report)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"\nSaved report to {out_path}")


if __name__ == "__main__":
    main()
