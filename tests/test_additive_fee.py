"""Additive fee: BASE on every swap + drain tax on top."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oscillon_fee import (
    BASE_FEE_BPS,
    drain_surcharge_bps,
    select_fee_bps,
)


def test_non_drain_is_base_only():
    assert select_fee_bps(50, False, fee_model="additive") == BASE_FEE_BPS
    assert select_fee_bps(0, False, fee_model="hybrid") == BASE_FEE_BPS


def test_drain_dead_band_no_tax():
    assert drain_surcharge_bps(2.7) == 0.0
    assert select_fee_bps(2, True, fee_model="additive") == BASE_FEE_BPS
    assert select_fee_bps(3, True, fee_model="additive") == 4.0


def test_drain_adds_tax_on_top():
    tax = drain_surcharge_bps(10)
    total = select_fee_bps(10, True, fee_model="additive")
    assert tax > 0
    assert abs(total - (BASE_FEE_BPS + tax)) < 0.001


def test_additive_matches_hybrid_totals():
    """Same total fee as integrated hybrid (base + tax ≡ single curve)."""
    for d in [0, 3, 5, 7, 10, 15, 20, 30, 50, 100]:
        hyb = select_fee_bps(d, True, fee_model="hybrid", k_override=45)
        add = select_fee_bps(d, True, fee_model="additive", k_override=45)
        assert abs(hyb - add) < 0.02, f"drift at {d} bps: hybrid={hyb} additive={add}"
