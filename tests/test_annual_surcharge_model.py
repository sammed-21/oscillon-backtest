"""Tests for state-weighted annual surcharge model."""

from pathlib import Path

import pandas as pd
import pytest

from src.annual_surcharge_model import (
    analyze_prepared_period,
    build_scenario,
    calibration_rates,
    dev_bucket,
    load_chainlink_daily_states,
    run_model,
    state_probabilities,
)

ROOT = Path(__file__).resolve().parents[1]


def test_dev_bucket_boundaries():
    assert dev_bucket(2.9) == "dead"
    assert dev_bucket(3.0) == "micro"
    assert dev_bucket(6.9) == "micro"
    assert dev_bucket(7.0) == "small"
    assert dev_bucket(29.9) == "medium"
    assert dev_bucket(30.0) == "large"


def test_march_stress_surcharge_order_of_magnitude():
    path = ROOT / "data/prepared_swaps_2023-03.csv"
    if not path.exists():
        pytest.skip("prepared march data missing")
    stats = analyze_prepared_period(path, "march")
    assert stats.lp_surcharge_usd > 200_000
    assert stats.lp_surcharge_per_day_by_bucket["large"] > 20_000


def test_calm_month_surcharge_small():
    path = ROOT / "data/prepared_swaps_2026-05.csv"
    if not path.exists():
        pytest.skip("prepared may data missing")
    stats = analyze_prepared_period(path, "may")
    assert stats.lp_surcharge_usd < 10_000
    assert stats.lp_surcharge_per_day_by_bucket["micro"] < 200


def test_base_scenario_apr_below_bull():
    chainlink = {
        "2023": ROOT / "data/chainlink_usdc_2023.csv",
        "2024": ROOT / "data/chainlink_usdc_2024.csv",
    }
    if not all(p.exists() for p in chainlink.values()):
        pytest.skip("chainlink files missing")
    _, _, _, scenarios = run_model(
        chainlink_files=chainlink,
        calm_prepared=ROOT / "data/prepared_swaps_2026-05.csv",
        stress_prepared=ROOT / "data/prepared_swaps_2023-03.csv",
    )
    tvl = 500_000_000
    assert scenarios["BASE"].apr_bps(tvl) < scenarios["BULL"].apr_bps(tvl)
    assert scenarios["BEAR"].apr_bps(tvl) < scenarios["BASE"].apr_bps(tvl)


def test_state_probabilities_sum_to_one():
    path = ROOT / "data/chainlink_usdc_2024.csv"
    if not path.exists():
        pytest.skip("2024 chainlink missing")
    daily = load_chainlink_daily_states(path)
    probs = state_probabilities(daily)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)


def test_build_scenario_annual_formula():
    daily = pd.Series(["dead", "micro", "micro"], index=[1, 2, 3])
    rates = {"dead": 0, "micro": 100, "small": 0, "medium": 0, "large": 0}
    scen = build_scenario("BASE", daily, rates, ["test"])
    # P(micro)=2/3, rate=100 → 365 * 2/3 * 100
    assert scen.annual_lp_surcharge_usd == pytest.approx(365 * 200 / 3, rel=1e-6)
