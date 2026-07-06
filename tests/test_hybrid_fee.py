"""Hybrid fee: max(piecewise, quadratic), monotonic on drain."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oscillon_fee import FeeContext, fee_bps, select_fee_bps, select_fee_pips


def test_hybrid_monotonic_drain():
    prev = 0.0
    for d in range(0, 201):
        f = select_fee_bps(d, True, fee_model="hybrid")
        assert f >= prev - 0.001
        prev = f


def test_hybrid_beats_piecewise_at_high_depeg():
    assert select_fee_bps(50, True, fee_model="hybrid") >= select_fee_bps(
        50, True, fee_model="piecewise"
    )


def test_hybrid_pips_matches_hook_bps():
    ctx = FeeContext(depeg_bps=30, is_drain=True, fee_model="hybrid", k_override=45)
    assert select_fee_pips(ctx) == 1000  # 10.00 bps hook integer


def test_hybrid_hook_fee_at_6_bps():
    ctx = FeeContext(depeg_bps=6, is_drain=True, fee_model="hybrid", k_override=45)
    assert fee_bps(select_fee_pips(ctx)) == 4.0
