"""
Optimize quadratic drain coefficient K.

score = lp_revenue - (λ1 × lvr) - (λ2 × volume_loss)

Uses src.oscillon_fee.select_fee_pips with k_override for each candidate K.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .backtest_engine import safe_dev_bps
from .oscillon_fee import BASE_FEE_BPS, FeeContext, fee_bps, select_fee_pips
from .swap_direction import validate_prepared_swaps
from .volume_model import retained_volume_fraction

VOLUME_LOSS_DEV_CAP_BPS = 50.0


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
    """Fee in bps for a given K (hook integer path via pips)."""
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
    """
    fixed, _ = validate_prepared_swaps(df)
    lp_revenue = 0.0
    lvr = 0.0
    volume_loss = 0.0
    fee_sum = 0.0
    n = 0

    for _, row in fixed.iterrows():
        dev = safe_dev_bps(row["dev_bps"])
        drain = bool(row["is_drain"])
        drain_size = float(row.get("drain_size_usd", row.get("swap_size_usd", 0.0)))
        restore_size = float(row.get("restore_size_usd", 0.0))
        total_size = drain_size + restore_size
        if total_size <= 0:
            continue

        f_bps = oscillon_fee_bps(int(dev), drain, k, pool_liquidity=pool_liquidity)
        retain = retained_volume_fraction(f_bps, competitor_fee_bps, eta=volume_eta)
        eff_drain = drain_size * retain
        eff_restore = restore_size * retain

        if drain:
            lp_revenue += eff_drain * f_bps / 10_000 + eff_restore * BASE_FEE_BPS / 10_000
            if dev > 0:
                lvr += max(0.0, eff_drain * (dev - f_bps) / 10_000)
            if f_bps > competitor_fee_bps and dev < VOLUME_LOSS_DEV_CAP_BPS:
                volume_loss += drain_size * (1.0 - retain)
        else:
            lp_revenue += (eff_drain + eff_restore) * f_bps / 10_000

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
