"""Fee model: quadratic surcharge path (float math)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oscillon_fee import BASE_FEE_PIPS, FeeContext, select_fee_pips


def test_base_fee_healthy():
    ctx = FeeContext(depeg_bps=0, is_drain=False)
    assert select_fee_pips(ctx) == BASE_FEE_PIPS


def test_restore_direction_during_depeg():
    ctx = FeeContext(depeg_bps=20, is_drain=False)
    assert select_fee_pips(ctx) == BASE_FEE_PIPS


def test_quadratic_drain_k45_at_15_bps():
    ctx = FeeContext(
        depeg_bps=15, is_drain=True, pool_liquidity=10**18, k_override=45, fee_model="quadratic"
    )
    assert select_fee_pips(ctx) == 465  # 3 + 1.648 surcharge


def test_quadratic_drain_k45():
    ctx = FeeContext(
        depeg_bps=20, is_drain=True, pool_liquidity=10**18, k_override=45, fee_model="quadratic"
    )
    assert select_fee_pips(ctx) == 530

    ctx50 = FeeContext(depeg_bps=50, is_drain=True, k_override=45, fee_model="quadratic")
    assert select_fee_pips(ctx50) == 1394

    ctx100 = FeeContext(depeg_bps=100, is_drain=True, k_override=45, fee_model="quadratic")
    assert select_fee_pips(ctx100) == 4634


def test_thin_pool_k60():
    ctx = FeeContext(
        depeg_bps=30, is_drain=True, pool_liquidity=100_000 * 10**6, fee_model="quadratic"
    )
    assert select_fee_pips(ctx) == 837
