"""Unit tests for src/saffron_lvr.py (Saffron Finance concentrated-liquidity LVR)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.saffron_lvr import SaffronLVR

TICK_LOWER = 2_375.0
TICK_UPPER = 2_625.0
FEE_TIER_BPS = 5.0


def test_out_of_range_returns_zero():
    vault = SaffronLVR()
    result = vault.calculate_per_swap(
        drain_volume_usd=10_000,
        pool_price=2_700.0,
        oracle_price=2_650.0,
        is_drain=True,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
    )
    assert result["in_range"] is False
    assert result["net_lvr_usd"] == 0.0


def test_in_range_calculates_correctly():
    vault = SaffronLVR()
    result = vault.calculate_per_swap(
        drain_volume_usd=10_000,
        pool_price=2_500.0,
        oracle_price=2_510.0,
        is_drain=True,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
    )
    expected_depeg_bps = abs(2_500.0 - 2_510.0) / 2_510.0 * 10_000
    expected_toxic = 10_000 * expected_depeg_bps / 10_000
    expected_fees = 10_000 * FEE_TIER_BPS / 10_000
    expected_net_lvr = max(0.0, expected_toxic - expected_fees)

    assert result["in_range"] is True
    assert result["depeg_bps"] == pytest.approx(expected_depeg_bps, rel=0.01)
    assert result["toxic_notional_usd"] == pytest.approx(expected_toxic, rel=0.01)
    assert result["fee_revenue_usd"] == pytest.approx(expected_fees, rel=0.01)
    assert result["net_lvr_usd"] == pytest.approx(expected_net_lvr, rel=0.01)


def test_non_drain_returns_zero():
    vault = SaffronLVR()
    result = vault.calculate_per_swap(
        drain_volume_usd=10_000,
        pool_price=2_500.0,
        oracle_price=2_510.0,
        is_drain=False,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
    )
    assert result["net_lvr_usd"] == 0.0


def test_below_noise_floor_returns_zero():
    vault = SaffronLVR()
    pool_price = 2_500.0
    oracle_price = pool_price / (1 - 3.0 / 10_000)
    result = vault.calculate_per_swap(
        drain_volume_usd=10_000,
        pool_price=pool_price,
        oracle_price=oracle_price,
        is_drain=True,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
        noise_floor_bps=5.0,
    )
    assert result["depeg_bps"] == pytest.approx(3.0, rel=1e-3)
    assert result["net_lvr_usd"] == 0.0


def test_vault_summary_apr_calculation():
    vault = SaffronLVR()
    swaps_df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-07-22", periods=10, freq="1min"),
            "drain_volume_usd": [10_000.0] * 10,
            "pool_price": [2_500.0] * 10,
            "oracle_price": [2_510.0] * 10,
            "is_drain": [True] * 10,
            "fee_income_usd": [5.0] * 10,
        }
    )

    summary = vault.calculate_vault_summary(
        swaps_df,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
        fixed_apr=12.0,
        vault_duration_days=1 / 365,
        tvl_usd=100_000.0,
    )

    assert summary["gross_yield_apr"] > 0
    assert summary["net_yield_apr"] < summary["gross_yield_apr"]
    assert summary["total_lvr_usd"] > 0


def test_out_of_range_swaps_earn_no_fee_income():
    """Out-of-range Uniswap v3 positions hold zero active liquidity and earn zero fees."""
    vault = SaffronLVR()
    swaps_df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-07-22", periods=4, freq="1min"),
            "drain_volume_usd": [10_000.0] * 4,
            # rows 0-1 in range [2375, 2625]; rows 2-3 out of range
            "pool_price": [2_500.0, 2_500.0, 3_000.0, 3_000.0],
            "oracle_price": [2_500.0, 2_500.0, 3_000.0, 3_000.0],
            "is_drain": [False] * 4,
            "fee_income_usd": [5.0, 5.0, 5.0, 5.0],
        }
    )

    summary = vault.calculate_vault_summary(
        swaps_df,
        tick_lower_price=TICK_LOWER,
        tick_upper_price=TICK_UPPER,
        fee_tier_bps=FEE_TIER_BPS,
        fixed_apr=12.0,
        vault_duration_days=1 / 365,
        tvl_usd=100_000.0,
    )

    assert summary["in_range_swaps"] == 2
    assert summary["out_of_range_swaps"] == 2
    assert summary["gross_fee_income_usd"] == pytest.approx(10.0)
    assert summary["fee_income_missed_out_of_range_usd"] == pytest.approx(10.0)
