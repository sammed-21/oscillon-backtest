"""Shared swap-level backtest economics (canonical mainnet path)."""

from __future__ import annotations

import pandas as pd

from .oscillon_fee import BASE_FEE_BPS, select_fee_bps
from .swap_direction import validate_prepared_swaps
from .volume_model import retained_volume_fraction

VOLUME_LOSS_DEV_CAP_BPS = 50.0


def safe_dev_bps(dev_bps: float) -> float:
    if dev_bps is None or (isinstance(dev_bps, float) and dev_bps != dev_bps):
        return 0.0
    return float(dev_bps)


def drain_routing_retention(
    fee_drain_bps: float,
    dev_bps: float,
    *,
    competitor_fee_bps: float,
    volume_eta: float = 2.0,
    apply_routing: bool = False,
) -> float:
    """
    Fraction of drain volume that still routes through this pool.

    When apply_routing=False (default), returns 1.0 — volume_lost is reported
  only as a counterfactual metric, not applied to LP income / LVR.
    """
    if not apply_routing:
        return 1.0
    if fee_drain_bps <= competitor_fee_bps or dev_bps >= VOLUME_LOSS_DEV_CAP_BPS:
        return 1.0
    return retained_volume_fraction(fee_drain_bps, competitor_fee_bps, eta=volume_eta)


def simulate_swap_row(
    row: pd.Series,
    fee_fn,
    *,
    curve_fee_bps: float = 4.0,
    volume_loss_dev_cap: float = VOLUME_LOSS_DEV_CAP_BPS,
    apply_routing: bool = False,
    volume_eta: float = 2.0,
) -> dict:
    """
    Per-minute LP income, LVR, and volume loss for one fee model.

    Conservation (drain leg, fee <= dev):
      lp_income + lvr = drain_size * dev_bps / 10_000
    Fee models only *split* the same depeg spread — they do not create new value.
    """
    dev = safe_dev_bps(row["dev_bps"])
    drain = bool(row["is_drain"])
    drain_size = float(row.get("drain_size_usd", row.get("swap_size_usd", 0)))
    restore_size = float(row.get("restore_size_usd", 0))
    total_size = drain_size + restore_size

    if drain:
        fee_drain = fee_fn(dev, True)
        retain = drain_routing_retention(
            fee_drain,
            dev,
            competitor_fee_bps=curve_fee_bps,
            volume_eta=volume_eta,
            apply_routing=apply_routing,
        )
        eff_drain = drain_size * retain
        if apply_routing:
            volume_lost = drain_size - eff_drain
        elif (
            fee_drain > curve_fee_bps and dev < volume_loss_dev_cap
        ):
            volume_lost = drain_size  # counterfactual: at risk if routers leave
        else:
            volume_lost = 0.0

        lp_income = (
            eff_drain * fee_drain / 10_000 + restore_size * BASE_FEE_BPS / 10_000
        )
        fee = fee_drain
        lvr = max(0.0, eff_drain * (dev - fee_drain) / 10_000) if dev > 0 else 0.0
    else:
        fee = fee_fn(dev, False)
        lp_income = total_size * fee / 10_000
        lvr = 0.0
        volume_lost = 0.0
        eff_drain = 0.0
        retain = 1.0

    spread_captured = lp_income + lvr

    return {
        "timestamp": row.get("timestamp"),
        "dev_bps": dev,
        "is_drain": drain,
        "swap_size": total_size,
        "drain_size": drain_size,
        "eff_drain_size": eff_drain if drain else 0.0,
        "restore_size": restore_size,
        "retain_frac": retain,
        "fee_bps": fee,
        "lp_income": lp_income,
        "lvr": lvr,
        "spread_captured": spread_captured,
        "arb_profit": lvr,
        "volume_lost": volume_lost,
    }


def run_prepared_backtest(
    swaps: pd.DataFrame,
    fee_fn,
    *,
    curve_fee_bps: float = 4.0,
    apply_routing: bool = False,
    volume_eta: float = 2.0,
) -> pd.DataFrame:
    fixed, _ = validate_prepared_swaps(swaps)
    records = [
        simulate_swap_row(
            row,
            fee_fn,
            curve_fee_bps=curve_fee_bps,
            apply_routing=apply_routing,
            volume_eta=volume_eta,
        )
        for _, row in fixed.iterrows()
    ]
    return pd.DataFrame(records)


def static_fee_fn(_dev: float, _is_drain: bool) -> float:
    return BASE_FEE_BPS


def hybrid_fee_fn(dev: float, is_drain: bool) -> float:
    return select_fee_bps(dev, is_drain, fee_model="hybrid", k_override=45)


def summarize_backtest(df: pd.DataFrame, *, tvl: float, period_days: float) -> dict:
    total_volume = df["swap_size"].sum()
    total_lp_income = df["lp_income"].sum()
    total_lvr = df["lvr"].sum()
    volume_lost = df["volume_lost"].sum()
    spread_captured = (
        df["spread_captured"].sum()
        if "spread_captured" in df.columns
        else total_lp_income + total_lvr
    )
    total_value = total_lp_income + total_lvr
    lp_capture_pct = (total_lp_income / total_value * 100) if total_value > 0 else 0.0
    volume_lost_pct = (volume_lost / total_volume * 100) if total_volume > 0 else 0.0
    stress_apr = (
        (total_lp_income / tvl) * (365 / period_days) * 100 if tvl > 0 else 0.0
    )
    return {
        "lp_income_usd": total_lp_income,
        "lvr_usd": total_lvr,
        "spread_captured_usd": spread_captured,
        "lp_capture_pct": lp_capture_pct,
        "volume_lost_usd": volume_lost,
        "volume_lost_pct": volume_lost_pct,
        "stress_apr": stress_apr,
        "period_days": period_days,
        "swaps": len(df),
        "drain_swaps": int(df["is_drain"].sum()),
    }
