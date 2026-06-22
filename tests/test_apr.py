"""APR: fee yield positive; net = fee − LVR."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.apr import annualize_compound, annualize_simple, compute_apr


def test_fee_apr_positive():
    r = compute_apr(
        fees_static_usd=1000,
        fees_dynamic_usd=1000,
        lp_net_lvr_usd_static=0,
        lp_net_lvr_usd_dynamic=0,
        lp_capital_usd=100_000,
        pool_tvl_usd=1_000_000,
        days=1,
    )
    # share 10% → LP earns $100 on $100k → 0.1% daily → ~36.5% APR
    assert r.fee_apr_static_pct > 0
    assert abs(r.fee_apr_static_pct - 36.5) < 1
    assert r.net_apr_static_pct == r.fee_apr_static_pct


def test_net_apr_fee_minus_lvr():
    r = compute_apr(
        fees_static_usd=100,
        fees_dynamic_usd=100,
        lp_net_lvr_usd_static=500,
        lp_net_lvr_usd_dynamic=400,
        lp_capital_usd=100_000,
        pool_tvl_usd=1_000_000,
        days=1,
    )
    assert r.fee_apr_static_pct > 0
    assert r.lvr_apr_static_pct > 0
    assert abs(r.net_apr_static_pct - (r.fee_apr_static_pct - r.lvr_apr_static_pct)) < 0.01


def test_apy_compounds_fee_yield():
    period = 0.01
    apy = annualize_compound(period, 1)
    apr = annualize_simple(period, 1)
    assert apy > apr
