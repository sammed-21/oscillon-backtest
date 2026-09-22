"""Unit tests for src/lp_inventory_replay.py (CLAMM inventory reconstruction)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.lp_inventory_replay import Q96, position_amounts, replay, sqrt_ratio_at_tick


def test_position_amounts_regimes():
    sa, sb = np.array([sqrt_ratio_at_tick(-10)]), np.array([sqrt_ratio_at_tick(10)])
    liq = np.array([1e18])
    x_lo, y_lo = position_amounts(sa[0] * 0.99, sa, sb, liq)   # below range: all token0
    x_hi, y_hi = position_amounts(sb[0] * 1.01, sa, sb, liq)   # above range: all token1
    x_mid, y_mid = position_amounts(1.0, sa, sb, liq)
    assert y_lo == 0 and x_lo > 0
    assert x_hi == 0 and y_hi > 0
    assert x_mid == pytest.approx(y_mid, rel=1e-6)  # symmetric range at peg: balanced


def _frames(sqrt_after: float, tick_after: int, liquidity: int):
    mods = pd.DataFrame(
        [{"blk": 1, "idx": 5, "sender": "0xa", "salt": "0x0", "tickLower": -10, "tickUpper": 10,
          "liquidityDelta": str(10**18)}]
    )
    sa, sb = np.array([sqrt_ratio_at_tick(-10)]), np.array([sqrt_ratio_at_tick(10)])
    L = np.array([1e18])
    x0, y0 = position_amounts(1.0, sa, sb, L)
    x1, y1 = position_amounts(sqrt_after, sa, sb, L)
    swaps = pd.DataFrame(
        [{"ts": "2026-01-01 00:00:00.000 UTC", "blk": 2, "idx": 1,
          "amount0": str(int(round(x0 - x1))), "amount1": str(int(round(y0 - y1))),  # v4 events: swapper perspective
          "sqrtPriceX96": str(int(sqrt_after * Q96)), "tick": tick_after, "liquidity": str(liquidity)}]
    )
    return swaps, mods


def test_replay_applies_mint_before_swap_and_matches_flow():
    swaps, mods = _frames(sqrt_after=sqrt_ratio_at_tick(4), tick_after=4, liquidity=10**18)
    res = replay(swaps, mods, init_sqrt_price_x96=Q96, decimals0=0, decimals1=0)
    f = res.frame.iloc[0]
    assert res.liquidity_match_rate == 1.0
    assert f.v0_pre > 0 and f.v1_pre > 0                       # mint (blk 1) applied before swap (blk 2)
    assert f.a0 < 0 < f.a1                                          # frame is pool-perspective: price up => token0 out, token1 in
    assert f.v0_post - f.v0_pre == pytest.approx(f.a0, rel=1e-6)
    assert f.v1_post - f.v1_pre == pytest.approx(f.a1, rel=1e-6)


def test_replay_flags_liquidity_mismatch():
    swaps, mods = _frames(sqrt_after=sqrt_ratio_at_tick(4), tick_after=4, liquidity=5 * 10**17)
    res = replay(swaps, mods, init_sqrt_price_x96=Q96, decimals0=0, decimals1=0)
    assert res.liquidity_match_rate == 0.0
