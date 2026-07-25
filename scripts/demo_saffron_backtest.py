#!/usr/bin/env python3
"""
Demonstration backtest for Saffron Finance vault LVR analysis.

Synthetic 2-hour ETH/USDG vault with concentrated liquidity (±5% range).
Shows how gross fee income overstates what the variable side earns once
LVR is subtracted, and whether the fixed APR was priced correctly.

Run: python3 scripts/demo_saffron_backtest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.saffron_lvr import SaffronLVR

# Vault parameters (2-hour ETH/USDG demo)
ETH_PRICE = 2_500.0
TICK_LOWER = 2_375.0  # -5%
TICK_UPPER = 2_625.0  # +5%
FEE_TIER_BPS = 5.0
FIXED_APR = 12.0
TVL_USD = 100_000.0
VAULT_DURATION_DAYS = 2 / 24  # 2 hours
N_SWAPS = 120
RNG = np.random.default_rng(42)


def generate_synthetic_swaps() -> pd.DataFrame:
    """Build 120 one-minute swaps with oscillating pool price and oracle lag."""
    timestamps = pd.date_range("2026-07-22T10:00:00", periods=N_SWAPS, freq="1min")

    # Pool price oscillates ±3% around $2,500
    t = np.arange(N_SWAPS)
    pool_prices = ETH_PRICE * (1 + 0.03 * np.sin(2 * np.pi * t / 30))

    # Oracle tracks pool with 10–30 bps lag (random per minute)
    lag_bps = RNG.uniform(10, 30, size=N_SWAPS)
    direction = RNG.choice([-1, 1], size=N_SWAPS)
    oracle_prices = pool_prices * (1 + direction * lag_bps / 10_000)

    is_drain = RNG.random(N_SWAPS) < 0.5
    drain_volumes = RNG.uniform(1_000, 50_000, size=N_SWAPS)
    fee_income = drain_volumes * (FEE_TIER_BPS / 10_000)

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "drain_volume_usd": drain_volumes,
            "pool_price": pool_prices,
            "oracle_price": oracle_prices,
            "is_drain": is_drain,
            "fee_income_usd": fee_income,
        }
    )


def pricing_verdict(pricing_error_bps: float) -> str:
    if abs(pricing_error_bps) <= 200:
        return "ACCURATE"
    if pricing_error_bps > 0:
        return "UNDERPRICED"
    return "OVERPRICED"


def main() -> None:
    swaps_df = generate_synthetic_swaps()
    engine = SaffronLVR()

    summary = engine.calculate_vault_summary(
        swaps_df,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
        fixed_apr=FIXED_APR,
        vault_duration_days=VAULT_DURATION_DAYS,
        tvl_usd=TVL_USD,
    )

    verdict = pricing_verdict(summary["pricing_error_bps"])
    pnl_sign = "+" if summary["variable_side_pnl_pct"] >= 0 else ""
    err_sign = "+" if summary["pricing_error_bps"] >= 0 else ""

    print()
    print("=== SAFFRON VAULT LVR ANALYSIS ===")
    print("Pair: ETH/USDG")
    print("Duration: 2 hours")
    print(f"TVL: ${TVL_USD:,.0f}")
    print(f"Tick range: ${TICK_LOWER:,.0f} to ${TICK_UPPER:,.0f} (±5%)")
    print(f"Fee tier: {FEE_TIER_BPS / 100:.2f}%")
    print()
    print("RESULTS:")
    print(f"Total swaps analyzed:      {summary['total_swaps']}")
    print(f"Time in range:             {summary['time_in_range_pct']:.1f}%")
    print(f"Gross fee income:          ${summary['gross_fee_income_usd']:,.2f}")
    print(f"LVR extracted:             ${summary['total_lvr_usd']:,.2f}")
    print(f"Net yield:                 ${summary['net_yield_usd']:,.2f}")
    print()
    print("YIELD METRICS:")
    print(f"Gross yield APR:           {summary['gross_yield_apr']:.2f}%")
    print(f"Net yield APR:             {summary['net_yield_apr']:.2f}%")
    print(f"Fixed APR offered:         {summary['fixed_apr_offered']:.2f}%")
    print(f"LVR as % of gross fees:    {summary['lvr_as_pct_of_gross_fees']:.2f}%")
    print()
    print("PRICING ASSESSMENT:")
    print(f"Variable side PnL:         {pnl_sign}{summary['variable_side_pnl_pct']:.2f}%")
    print(f"Pricing error:             {err_sign}{summary['pricing_error_bps']:.0f} bps")
    print(f"Verdict: {verdict}")
    print("(Accurate = pricing error within ±200 bps)")
    print()


if __name__ == "__main__":
    main()
