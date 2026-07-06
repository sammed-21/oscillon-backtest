"""Canonical swap-level backtest economics."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest_engine import hybrid_fee_fn, run_prepared_backtest, simulate_swap_row, static_fee_fn, summarize_backtest
from src.oscillon_fee import fee_bps, FeeContext, select_fee_pips


def test_hook_fee_6bps_is_4():
    ctx = FeeContext(depeg_bps=6, is_drain=True, fee_model="hybrid")
    assert fee_bps(select_fee_pips(ctx)) == 4.0


def test_drain_leg_only_lvr():
    row = pd.Series(
        {
            "dev_bps": 20.0,
            "is_drain": True,
            "drain_size_usd": 1000.0,
            "restore_size_usd": 500.0,
        }
    )
    df = run_prepared_backtest(pd.DataFrame([row]), static_fee_fn)
    assert df.iloc[0]["lvr"] == 1000.0 * (20 - 3) / 10_000
    assert df.iloc[0]["lp_income"] == 1000.0 * 3 / 10_000 + 500.0 * 3 / 10_000


def test_volume_loss_only_when_routing_off():
    row = pd.Series(
        {
            "dev_bps": 20.0,
            "is_drain": True,
            "drain_size_usd": 1000.0,
            "restore_size_usd": 0.0,
        }
    )
    off = simulate_swap_row(row, hybrid_fee_fn, curve_fee_bps=4.0, apply_routing=False)
    assert off["lp_income"] + off["lvr"] == pytest.approx(1000.0 * 20 / 10_000)
    assert off["volume_lost"] == 0.0


def test_volume_loss_applied_with_routing():
    row = pd.Series(
        {
            "dev_bps": 20.0,
            "is_drain": True,
            "drain_size_usd": 1000.0,
            "restore_size_usd": 0.0,
        }
    )
    on = simulate_swap_row(row, hybrid_fee_fn, curve_fee_bps=4.0, apply_routing=True)
    assert on["volume_lost"] > 0.0
    assert on["lp_income"] < simulate_swap_row(
        row, hybrid_fee_fn, apply_routing=False
    )["lp_income"]


def test_summarize_apr_annualizes():
    df = pd.DataFrame({"swap_size": [100], "lp_income": [10], "lvr": [0], "is_drain": [False], "volume_lost": [0]})
    s = summarize_backtest(df, tvl=1000, period_days=10)
    assert abs(s["stress_apr"] - (10 / 1000 * 365 / 10 * 100)) < 0.01
