"""Piecewise surcharge curve (float math)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oscillon_fee import BASE_FEE_BPS, BASE_FEE_PIPS, FeeContext, select_fee_bps, select_fee_pips


def test_piecewise_dead_band_is_base_only():
    assert select_fee_bps(0, True, fee_model="piecewise") == BASE_FEE_BPS
    assert select_fee_bps(2, True, fee_model="piecewise") == BASE_FEE_BPS
    assert select_fee_pips(FeeContext(2, True, fee_model="piecewise")) == BASE_FEE_PIPS


def test_piecewise_surcharge_starts_at_small_depeg():
    assert select_fee_bps(3, True, fee_model="piecewise") == 4.0


def test_piecewise_calibration_targets():
    assert abs(select_fee_bps(10, True, fee_model="piecewise") - 5.0) < 0.01
    assert abs(select_fee_bps(20, True, fee_model="piecewise") - 9.90) < 0.01
    assert abs(select_fee_bps(30, True, fee_model="piecewise") - 11.0) < 0.01
    assert abs(select_fee_bps(50, True, fee_model="piecewise") - 13.20) < 0.01


def test_piecewise_restore_is_base():
    assert select_fee_bps(30, False, fee_model="piecewise") == BASE_FEE_BPS


def test_piecewise_cap():
    assert select_fee_bps(500, True, fee_model="piecewise") == 50.0
