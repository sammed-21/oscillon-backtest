#!/usr/bin/env python3
"""
Backtest Oscillon quadratic drain fee vs static 1 bps on minute swap data.

  python3 scripts/backtest.py --start 2026-06-01 --end 2026-06-01
  python3 scripts/backtest.py --start 2026-06-01 --end 2026-06-01 --apr
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.data_loader import DEFAULT_CHAIN, DEFAULT_POOL, load_minute_range
from src.depeg_analysis import run_full_comparison
from src.oscillon_fee import FeeContext, fee_bps, select_fee_pips
from src.scenario import DepegScenario, inject_fake_depeg

OUT = ROOT / "output"


def parse_date(s: str) -> date:
    parts = s.strip().split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def with_injected_stress(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    return inject_fake_depeg(
        df,
        DepegScenario(
            oracle_usd=args.oracle_usd,
            pool_lag_bps=args.pool_lag_bps,
            duration_minutes=args.stress_minutes,
        ),
    )


def with_chainlink_oracle(df: pd.DataFrame, oracle_csv: str) -> pd.DataFrame:
    """
    Build stress fields from real Chainlink oracle data (Dune CSV), no dummy injection.
    Requires columns: minute, usdc_price
    """
    oracle = pd.read_csv(oracle_csv)
    # Accept both schemas:
    # 1) minute, usdc_price
    # 2) call_block_time, price
    if "minute" not in oracle.columns and "call_block_time" in oracle.columns:
        oracle = oracle.rename(columns={"call_block_time": "minute"})
    if "usdc_price" not in oracle.columns and "price" in oracle.columns:
        oracle = oracle.rename(columns={"price": "usdc_price"})

    needed = {"minute", "usdc_price"}
    if not needed.issubset(set(oracle.columns)):
        raise ValueError(
            f"{oracle_csv} must contain either "
            "['minute','usdc_price'] or ['call_block_time','price']"
        )

    oracle["timestamp"] = pd.to_datetime(oracle["minute"], utc=True).dt.tz_localize(None)
    oracle = oracle.sort_values("timestamp")

    out = (
        df.reset_index()
        .rename(columns={"index": "timestamp"})
        .sort_values("timestamp")
    )
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).dt.tz_localize(None)
    out = pd.merge_asof(
        out,
        oracle[["timestamp", "usdc_price"]],
        on="timestamp",
        direction="backward",
    )

    if out["usdc_price"].notna().sum() == 0:
        raise ValueError(
            "No oracle rows matched data timestamps. Check your date range / oracle CSV."
        )

    out["chainlink_usd"] = out["usdc_price"].fillna(1.0).astype(float)
    out["oracle_depeg_bps"] = (abs(1.0 - out["chainlink_usd"]) * 10_000).astype(int)
    out["pool_depeg_bps"] = (abs(1.0 - out["price"]) * 10_000).astype(int)
    out["arb_gap_bps"] = (out["oracle_depeg_bps"] - out["pool_depeg_bps"]).clip(lower=0).astype(int)

    return out.set_index("timestamp")


def main() -> None:
    p = argparse.ArgumentParser(description="Oscillon fee mechanism backtest")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--chain", default=DEFAULT_CHAIN)
    p.add_argument("--pool", default=DEFAULT_POOL)
    p.add_argument("--start", required=True, type=parse_date)
    p.add_argument("--end", required=True, type=parse_date)
    p.add_argument("--oracle-usd", type=float, default=0.937)
    p.add_argument("--pool-lag-bps", type=int, default=15)
    p.add_argument("--stress-minutes", type=int, default=180)
    p.add_argument("--k", type=int, default=45)
    p.add_argument(
        "--fee-model",
        choices=["piecewise", "quadratic", "hybrid"],
        default="hybrid",
        help="Dynamic fee curve (quadratic uses --k)",
    )
    p.add_argument(
        "--mode",
        choices=["injected", "oracle"],
        default="injected",
        help="injected=dummy stress injection, oracle=use real Chainlink CSV",
    )
    p.add_argument(
        "--oracle-csv",
        default=str(ROOT / "data" / "chainlink_usdc_2023.csv"),
        help="Dune Chainlink CSV used when --mode oracle",
    )
    p.add_argument("--apr", action="store_true", help="Run APR/APY after backtest")
    p.add_argument("--capital", type=float, default=100_000)
    p.add_argument("--tvl", type=float, default=2_500_000)
    args = p.parse_args()

    if args.end < args.start:
        p.error("--end must be on or after --start")

    df = load_minute_range(
        args.data_dir, args.chain, args.pool, args.start, args.end
    )

    if args.mode == "oracle":
        stressed = with_chainlink_oracle(df, args.oracle_csv)
    else:
        stressed = with_injected_stress(df, args)

    _, summary = run_full_comparison(
        stressed, k=args.k, fee_model=args.fee_model
    )
    days = (args.end - args.start).days + 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))

    print("Oscillon fee backtest")
    print(f"  Data: {args.start} → {args.end} ({days}d)")
    if args.mode == "oracle":
        print(f"  Mode: real Chainlink oracle from {args.oracle_csv}, K={args.k}")
    else:
        print(
            f"  Mode: injected stress | oracle ${args.oracle_usd:.4f}, "
            f"pool lag {args.pool_lag_bps} bps, K={args.k}"
        )
    print()
    print("  LP net loss (static 1 bps):  ${:,.2f}".format(summary["lp_net_loss_static_usd"]))
    print("  LP net loss (Oscillon):      ${:,.2f}".format(summary["lp_net_loss_dynamic_usd"]))
    print("  LVR reduced:               ${:,.2f} ({:.1f}%)".format(
        summary["lp_loss_reduced_usd"], summary["lp_loss_reduction_pct"]
    ))
    print("  Fees (static / dynamic):   ${:,.2f} / ${:,.2f}".format(
        summary["fees_collected_static_usd"], summary["fees_collected_dynamic_usd"]
    ))
    print(f"\n  Wrote {OUT / 'summary.json'}")

    print("\n  Fee (bps) on drain swaps:")
    for d in (0, 7, 15, 30, 50,100):
        pips = select_fee_pips(
            FeeContext(
                d, d >= 7, k_override=args.k, fee_model=args.fee_model
            )
        )
        print(f"    {d:>3} bps depeg → {fee_bps(pips):.2f} bps fee")

    if args.apr:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/calc_apr.py"),
                "--summary",
                str(OUT / "summary.json"),
                "--days",
                str(days),
                "--capital",
                str(args.capital),
                "--tvl",
                str(args.tvl),
            ],
            check=False,
        )


if __name__ == "__main__":
    main()
