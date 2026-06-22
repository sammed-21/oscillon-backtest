"""
APR / APY for LP backtest results (standard yield definitions).

Period return (fraction of LP capital over `days`):

  fee_yield   = fee_income_usd / lp_capital     (what you earn from fees)
  lvr_drag    = adverse_selection_usd / lp_capital  (what you lose to toxic flow)
  net_return  = fee_yield - lvr_drag            (= LP PnL / capital)

Annualization (365-day year):

  APR  = period_return × (365 / days)           simple, no compounding
  APY  = (1 + period_return)^(365 / days) − 1   compound within the year

Fee APR/APY are always the positive swap-fee yield. Net APR/APY can be
negative when modeled LVR exceeds fees (common under injected stress).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AprResult:
    days: float
    lp_capital_usd: float
    pool_tvl_usd: float
    lp_share: float
    # LP dollar amounts over the period (pro-rata share of pool)
    lp_fees_static_usd: float
    lp_fees_dynamic_usd: float
    lp_lvr_drag_static_usd: float
    lp_lvr_drag_dynamic_usd: float
    lp_net_pnl_static_usd: float
    lp_net_pnl_dynamic_usd: float
    # Fee yield (positive earnings from trading fees)
    fee_apr_static_pct: float
    fee_apr_dynamic_pct: float
    fee_apy_static_pct: float
    fee_apy_dynamic_pct: float
    # LVR drag (positive % = cost; not subtracted again from fee APR)
    lvr_apr_static_pct: float
    lvr_apr_dynamic_pct: float
    # Net = fee yield − LVR drag
    net_apr_static_pct: float
    net_apr_dynamic_pct: float
    net_apy_static_pct: float
    net_apy_dynamic_pct: float
    # Pool-wide fee yield (DeFi dashboard style)
    pool_fee_apr_static_pct: float
    pool_fee_apr_dynamic_pct: float
    net_apr_improvement_bps: float


def annualize_simple(period_return: float, days: float) -> float:
    if days <= 0:
        return 0.0
    return period_return * (365.0 / days) * 100.0


def annualize_compound(period_return: float, days: float) -> float:
    if days <= 0:
        return 0.0
    if period_return <= -1.0:
        return -100.0
    periods_per_year = 365.0 / days
    return ((1.0 + period_return) ** periods_per_year - 1.0) * 100.0


def compute_apr(
    *,
    fees_static_usd: float,
    fees_dynamic_usd: float,
    lp_net_lvr_usd_static: float,
    lp_net_lvr_usd_dynamic: float,
    lp_capital_usd: float,
    pool_tvl_usd: float,
    days: float = 1.0,
) -> AprResult:
    """
    `lp_net_lvr_usd_*` = pool-level (toxic − fees); positive means LP lost money.
    """
    if pool_tvl_usd <= 0 or lp_capital_usd <= 0:
        raise ValueError("pool_tvl_usd and lp_capital_usd must be positive")

    share = lp_capital_usd / pool_tvl_usd

    lp_fees_s = fees_static_usd * share
    lp_fees_d = fees_dynamic_usd * share
    # Adverse selection after fees (always ≥ 0 when the model reports a loss)
    lp_lvr_s = max(0.0, lp_net_lvr_usd_static * share)
    lp_lvr_d = max(0.0, lp_net_lvr_usd_dynamic * share)
    lp_pnl_s = lp_fees_s - lp_lvr_s
    lp_pnl_d = lp_fees_d - lp_lvr_d

    fee_yield_s = lp_fees_s / lp_capital_usd
    fee_yield_d = lp_fees_d / lp_capital_usd
    lvr_drag_s = lp_lvr_s / lp_capital_usd
    lvr_drag_d = lp_lvr_d / lp_capital_usd
    net_ret_s = lp_pnl_s / lp_capital_usd
    net_ret_d = lp_pnl_d / lp_capital_usd

    fee_apr_s = annualize_simple(fee_yield_s, days)
    fee_apr_d = annualize_simple(fee_yield_d, days)
    lvr_apr_s = annualize_simple(lvr_drag_s, days)
    lvr_apr_d = annualize_simple(lvr_drag_d, days)
    net_apr_s = annualize_simple(net_ret_s, days)
    net_apr_d = annualize_simple(net_ret_d, days)

    pool_fee_yield_s = fees_static_usd / pool_tvl_usd
    pool_fee_yield_d = fees_dynamic_usd / pool_tvl_usd

    return AprResult(
        days=days,
        lp_capital_usd=lp_capital_usd,
        pool_tvl_usd=pool_tvl_usd,
        lp_share=share,
        lp_fees_static_usd=lp_fees_s,
        lp_fees_dynamic_usd=lp_fees_d,
        lp_lvr_drag_static_usd=lp_lvr_s,
        lp_lvr_drag_dynamic_usd=lp_lvr_d,
        lp_net_pnl_static_usd=lp_pnl_s,
        lp_net_pnl_dynamic_usd=lp_pnl_d,
        fee_apr_static_pct=fee_apr_s,
        fee_apr_dynamic_pct=fee_apr_d,
        fee_apy_static_pct=annualize_compound(fee_yield_s, days),
        fee_apy_dynamic_pct=annualize_compound(fee_yield_d, days),
        lvr_apr_static_pct=lvr_apr_s,
        lvr_apr_dynamic_pct=lvr_apr_d,
        net_apr_static_pct=net_apr_s,
        net_apr_dynamic_pct=net_apr_d,
        net_apy_static_pct=annualize_compound(net_ret_s, days),
        net_apy_dynamic_pct=annualize_compound(net_ret_d, days),
        pool_fee_apr_static_pct=annualize_simple(pool_fee_yield_s, days),
        pool_fee_apr_dynamic_pct=annualize_simple(pool_fee_yield_d, days),
        net_apr_improvement_bps=(net_apr_d - net_apr_s) * 100,
    )
