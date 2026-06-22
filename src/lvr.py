"""
Loss-versus-rebalancing (LVR) proxy for stablecoin LP backtests.

We model adverse selection on *drain-direction* flow during depegs:
  toxic_notional ≈ drain_volume_usd × (depeg_bps / 10_000)

Fees collected on that flow partially offset toxic flow:
  net_lvr_usd = toxic_notional − fee_revenue_usd

This is a research approximation — not a full AMM inventory mark-to-market.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LvrMinute:
    toxic_usd: float
    fee_usd: float
    drain_volume_usd: float
    depeg_bps: int


def minute_lvr(
    drain_volume_usd: float,
    depeg_bps: int,
    fee_bps: float,
) -> LvrMinute:
    if drain_volume_usd <= 0 or depeg_bps <= 5:
        return LvrMinute(0.0, 0.0, drain_volume_usd, depeg_bps)

    toxic = drain_volume_usd * (depeg_bps / 10_000)
    fee_rev = drain_volume_usd * (fee_bps / 10_000)
    return LvrMinute(toxic, fee_rev, drain_volume_usd, depeg_bps)


def net_lvr_usd(row: LvrMinute) -> float:
    return row.toxic_usd - row.fee_usd
