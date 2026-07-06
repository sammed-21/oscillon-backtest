"""Supply-shock scenario: zero-cost-basis mint-and-dump."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.supply_shock_analysis import run_supply_shock_comparison
from src.supply_shock_scenario import (
    SupplyShockScenario,
    calibrate_pool_depth_for_target_proceeds,
    simulate_supply_shock,
    stabl_r_default,
)


def test_stabl_r_default_gross_near_reported():
    sc = stabl_r_default()
    static = simulate_supply_shock(sc, fee_mode="static")
    assert 2_600_000 < static.gross_proceeds_usd < 3_000_000


def test_fee_escalation_barely_moves_attacker_payoff():
    comparison = run_supply_shock_comparison(stabl_r_default())
    assert abs(comparison.attacker_payoff_delta_pct) < 1.0


def test_dynamic_fees_exceed_static():
    comparison = run_supply_shock_comparison(stabl_r_default())
    assert comparison.dynamic.total_fees_paid_usd > comparison.static.total_fees_paid_usd


def test_multi_block_dump():
    sc = SupplyShockScenario(
        mint_face_value=1_000_000,
        dump_blocks=10,
        pool_liquidity_depth=500_000,
        initial_dev_bps=6.0,
    )
    result = simulate_supply_shock(sc, fee_mode="dynamic")
    assert len(result.trades) == 10
    assert result.remaining_mint_unsold_usd == 0.0


def test_calibrate_pool_depth():
    depth = calibrate_pool_depth_for_target_proceeds(10_400_000, 2_800_000)
    sc = SupplyShockScenario(
        mint_face_value=10_400_000,
        pool_liquidity_depth=depth,
        initial_dev_bps=6.0,
    )
    gross = simulate_supply_shock(sc, fee_mode="static").gross_proceeds_usd
    assert abs(gross - 2_800_000) < 50_000
