"""Drain direction must match token0=USDC net inflow during below-peg oracle."""

from __future__ import annotations

import pandas as pd
import pytest

from src.swap_direction import (
    TOKEN0_SYMBOL,
    classify_prepared_swaps,
    is_drain_swap,
    swap_direction,
    validate_prepared_swaps,
)


def test_token0_is_usdc():
    assert TOKEN0_SYMBOL == "USDC"


def test_swap_direction_signs():
    assert swap_direction(100) == "sell_usdc"
    assert swap_direction(-100) == "buy_usdc"
    assert swap_direction(0) == "flat"


def test_drain_requires_usdc_in_not_usdt_in():
    # peg below, USDC sold in → drain
    assert is_drain_swap(0.997, net_amount0=1_000_000) is True
    # peg below, USDC bought out (USDT in) → NOT drain
    assert is_drain_swap(0.997, net_amount0=-1_000_000) is False
    # peg at par → not drain regardless of direction
    assert is_drain_swap(1.0, net_amount0=1_000_000) is False


def test_drain_nav_mode_below_nav():
    """NAV mode: drain when pool prices token0 below NAV and token0 sold in."""
    assert is_drain_swap(114.0, 1_000, pool_price=113.5, reference_mode="nav")
    assert not is_drain_swap(114.0, 1_000, pool_price=114.5, reference_mode="nav")
    df = pd.DataFrame(
        [
            {
                "netAmount0": 1_000_000,
                "netAmount1": 0,
                "oracle_price": 114.0,
                "pool_price": 113.5,
            },
        ]
    )
    out = classify_prepared_swaps(df, reference_mode="nav")
    assert out.iloc[0]["is_drain"]
    assert out.iloc[0]["reference_mode"] == "nav"


def test_classify_prepared_swaps_may_row_pattern():
    """Mirror a real May minute: USDT in / USDC out must NOT be drain."""
    df = pd.DataFrame(
        [
            {
                "netAmount0": -409_955_217,
                "netAmount1": 410_047_033,
                "inAmount0": 0,
                "inAmount1": 410_047_033,
                "oracle_price": 0.99973,
            },
            {
                "netAmount0": 23_189_037_140,
                "netAmount1": -23_189_586_692,
                "inAmount0": 23_189_037_140,
                "inAmount1": 0,
                "oracle_price": 0.99973,
            },
        ]
    )
    out = classify_prepared_swaps(df)
    assert out.iloc[0]["swap_direction"] == "buy_usdc"
    assert not out.iloc[0]["is_drain"]
    assert out.iloc[1]["swap_direction"] == "sell_usdc"
    assert out.iloc[1]["is_drain"]


def test_drain_token1_leg_usdt_oracle():
    """USDT oracle leg: peg below + USDT sold in (netAmount1 > 0)."""
    from src.swap_direction import is_drain_swap_token1, classify_prepared_swaps

    assert is_drain_swap_token1(0.998, net_amount1=1_000_000) is True
    assert is_drain_swap_token1(0.998, net_amount1=-1_000_000) is False
    df = pd.DataFrame(
        [
            {
                "netAmount0": -410_047_033,
                "netAmount1": 410_047_033,
                "oracle_price": 0.99973,
            },
        ]
    )
    out = classify_prepared_swaps(df, oracle_leg="token1")
    assert out.iloc[0]["is_drain"]
    assert out.iloc[0]["oracle_asset"] == "USDT"
    assert out.iloc[0]["drain_size_usd"] == pytest.approx(410.047033)


def test_validate_fixes_inverted_legacy_is_drain():
    df = pd.DataFrame(
        [
            {
                "netAmount0": -100,
                "inAmount0": 0,
                "inAmount1": 100,
                "oracle_price": 0.99,
                "is_drain": True,
                "swap_size_usd": 1.0,
            }
        ]
    )
    fixed, mismatches = validate_prepared_swaps(df)
    assert mismatches == 1
    assert not fixed.iloc[0]["is_drain"]
