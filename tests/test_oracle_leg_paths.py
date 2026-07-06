"""Tests for USDC vs USDT oracle leg path helpers."""

from src.oracle_leg_paths import (
    LEG_TAGS,
    backtest_outputs,
    prepared_csv,
)


def test_prepared_paths_differ_by_leg():
    usdc = prepared_csv("2026-01-01", "2026-06-30", "token0")
    usdt = prepared_csv("2026-01-01", "2026-06-30", "token1")
    assert usdc != usdt
    assert LEG_TAGS["token0"] in str(usdc)
    assert LEG_TAGS["token1"] in str(usdt)


def test_backtest_outputs_differ_by_leg():
    u0 = backtest_outputs("2026-01-01", "2026-06-30", "token0")
    u1 = backtest_outputs("2026-01-01", "2026-06-30", "token1")
    assert u0["chart"] != u1["chart"]
    assert "usdc_oracle" in str(u0["chart"])
    assert "usdt_oracle" in str(u1["chart"])
