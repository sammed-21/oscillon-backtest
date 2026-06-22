"""
Optimize quadratic drain coefficient K.

score = lp_revenue - (λ1 × lvr) - (λ2 × volume_loss)

Uses src.oscillon_fee.select_fee_pips with k_override for each candidate K.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .oscillon_fee import FeeContext, fee_bps, select_fee_pips
from .volume_model import retained_volume_fraction


@dataclass
class KScoreResult:
    k: int
    lp_revenue_usd: float
    lvr_usd: float
    volume_loss_usd: float
    score: float
    avg_fee_bps: float
    minutes: int


def oscillon_fee_bps(
    depeg_bps: int,
    is_drain: bool,
    k: int,
    *,
    pool_liquidity: int = 10**18,
) -> float:
    """Fee in bps for a given K (wraps oscillon_fee.py)."""
    pips = select_fee_pips(
        FeeContext(
            depeg_bps=int(depeg_bps),
            is_drain=bool(is_drain),
            pool_liquidity=pool_liquidity,
            k_override=int(k),
            fee_model="quadratic",
        )
    )
    return fee_bps(pips)


def evaluate_k_on_prepared(
    df: pd.DataFrame,
    k: int,
    *,
    lambda_lvr: float = 1.0,
    lambda_volume: float = 0.5,
    competitor_fee_bps: float = 4.0,
    volume_eta: float = 2.0,
    pool_liquidity: int = 10**18,
) -> KScoreResult:
    """
    Aggregate score for one K over prepared_swaps rows (minute-level).

    - lp_revenue: fees on retained volume
    - lvr: max(0, dev - fee) × volume on drain minutes (arb extraction proxy)
    - volume_loss: swap_size × (1 - retain) when routers skip high-fee pool
    """
    lp_revenue = 0.0
    lvr = 0.0
    volume_loss = 0.0
    fee_sum = 0.0
    n = 0

    for _, row in df.iterrows():
        dev = int(row["dev_bps"])
        drain = bool(row["is_drain"])
        size = float(row.get("swap_size_usd", 0.0))
        if size <= 0:
            continue

        f_bps = oscillon_fee_bps(dev, drain, k, pool_liquidity=pool_liquidity)
        retain = retained_volume_fraction(f_bps, competitor_fee_bps, eta=volume_eta)
        eff_size = size * retain

        lp_revenue += eff_size * (f_bps / 10_000.0)
        if drain and dev > 0:
            lvr += max(0.0, eff_size * (dev - f_bps) / 10_000.0)
        volume_loss += size * (1.0 - retain)
        fee_sum += f_bps
        n += 1

    score = lp_revenue - (lambda_lvr * lvr) - (lambda_volume * volume_loss)
    avg_fee = fee_sum / n if n else 0.0

    return KScoreResult(
        k=k,
        lp_revenue_usd=lp_revenue,
        lvr_usd=lvr,
        volume_loss_usd=volume_loss,
        score=score,
        avg_fee_bps=avg_fee,
        minutes=n,
    )


def sweep_k_values(
    df: pd.DataFrame,
    k_values: list[int] | None = None,
    *,
    lambda_lvr: float = 1.0,
    lambda_volume: float = 0.5,
    competitor_fee_bps: float = 4.0,
    volume_eta: float = 2.0,
) -> pd.DataFrame:
    """Run evaluate_k_on_prepared for each K; return sorted results table."""
    if k_values is None:
        k_values = [20, 30, 45, 60, 80]

    rows = [
        evaluate_k_on_prepared(
            df,
            k,
            lambda_lvr=lambda_lvr,
            lambda_volume=lambda_volume,
            competitor_fee_bps=competitor_fee_bps,
            volume_eta=volume_eta,
        )
        for k in k_values
    ]
    out = pd.DataFrame([r.__dict__ for r in rows])
    return out.sort_values("score", ascending=False).reset_index(drop=True)
