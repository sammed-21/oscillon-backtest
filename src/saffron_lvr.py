"""
LVR calculation for Saffron Finance concentrated-liquidity vaults.

How this differs from the Oscillon stablecoin backtest (src/lvr.py):

  1. Concentrated liquidity range gating.
     Oscillon's USDC/USDT positions are effectively full-range, so every
     minute of drain volume is exposed to LVR. Saffron vaults sit on tight
     Uniswap V3 tick ranges; when the pool price trades outside
     [tick_lower_price, tick_upper_price] the vault holds zero active
     liquidity and therefore has zero adverse-selection exposure. This
     module gates LVR to zero whenever the pool price is out of range.

  2. General volatile-pair depeg signal.
     Oscillon assumes the peg is always $1.00, so
     depeg_bps = |pool_price - 1.00| * 10_000. Saffron vaults can wrap any
     pair (e.g. ETH/USDG, USDG/NVDA), where there is no $1.00 peg. This
     module uses the general form against an external oracle price:
       depeg_bps = |pool_price - oracle_price| / oracle_price * 10_000
     The stablecoin formula is the special case oracle_price == 1.00.

  3. Vault-specific summary metrics.
     Oscillon reports LP capture % and annualised surcharge APR. Saffron
     vaults are fixed-term, so this module reports gross fee income, total
     LVR extracted, net yield (in USD and annualised APR), how that net
     yield compares to the fixed rate offered to the vault's LPs, LVR as a
     percentage of gross fees, and time spent in range vs out of range.
"""

from __future__ import annotations

import pandas as pd

DAYS_PER_YEAR = 365.0


class SaffronLVR:
    """LVR engine for Saffron fixed-term concentrated-liquidity vaults."""

    def calculate_per_swap(
        self,
        drain_volume_usd: float,
        pool_price: float,
        oracle_price: float,
        is_drain: bool,
        tick_lower_price: float,
        tick_upper_price: float,
        fee_tier_bps: float,
        noise_floor_bps: float = 5.0,
    ) -> dict:
        """
        Compute LVR for a single swap against a concentrated position.

        Returns in_range, depeg_bps, toxic_notional_usd, fee_revenue_usd,
        net_lvr_usd. Out-of-range, non-drain, and sub-noise-floor swaps
        return zero LVR components (depeg_bps is still reported).
        """
        in_range = tick_lower_price <= pool_price <= tick_upper_price

        if oracle_price <= 0:
            depeg_bps = 0.0
        else:
            depeg_bps = abs(pool_price - oracle_price) / oracle_price * 10_000

        if (
            not in_range
            or not is_drain
            or drain_volume_usd <= 0
            or depeg_bps <= noise_floor_bps
        ):
            return {
                "in_range": in_range,
                "depeg_bps": depeg_bps,
                "toxic_notional_usd": 0.0,
                "fee_revenue_usd": 0.0,
                "net_lvr_usd": 0.0,
            }

        toxic_notional_usd = drain_volume_usd * (depeg_bps / 10_000)
        fee_revenue_usd = drain_volume_usd * (fee_tier_bps / 10_000)
        net_lvr_usd = max(0.0, toxic_notional_usd - fee_revenue_usd)

        return {
            "in_range": in_range,
            "depeg_bps": depeg_bps,
            "toxic_notional_usd": toxic_notional_usd,
            "fee_revenue_usd": fee_revenue_usd,
            "net_lvr_usd": net_lvr_usd,
        }

    def calculate_vault_summary(
        self,
        swaps_df: pd.DataFrame,
        tick_lower_price: float,
        tick_upper_price: float,
        fee_tier_bps: float,
        fixed_apr: float,
        vault_duration_days: float,
        tvl_usd: float,
    ) -> dict:
        """
        Aggregate per-swap LVR over a vault term and compare net yield to
        the fixed APR offered to the fixed side.

        Expected swaps_df columns:
          timestamp, drain_volume_usd, pool_price, oracle_price,
          is_drain, fee_income_usd
        """
        total_swaps = len(swaps_df)
        in_range_swaps = 0
        gross_fee_income_usd = 0.0
        fee_income_missed_out_of_range_usd = 0.0
        total_lvr_usd = 0.0

        for row in swaps_df.itertuples(index=False):
            result = self.calculate_per_swap(
                drain_volume_usd=float(row.drain_volume_usd),
                pool_price=float(row.pool_price),
                oracle_price=float(row.oracle_price),
                is_drain=bool(row.is_drain),
                tick_lower_price=tick_lower_price,
                tick_upper_price=tick_upper_price,
                fee_tier_bps=fee_tier_bps,
            )
            row_fee_income = float(row.fee_income_usd)
            if result["in_range"]:
                in_range_swaps += 1
                gross_fee_income_usd += row_fee_income
            else:
                # Out-of-range Uniswap v3 positions hold zero active liquidity —
                # they earn no fees. Track what was missed for reporting only.
                fee_income_missed_out_of_range_usd += row_fee_income
            total_lvr_usd += result["net_lvr_usd"]

        out_of_range_swaps = total_swaps - in_range_swaps
        time_in_range_pct = (in_range_swaps / total_swaps * 100.0) if total_swaps else 0.0
        net_yield_usd = gross_fee_income_usd - total_lvr_usd

        if vault_duration_days > 0 and tvl_usd > 0:
            annualisation = DAYS_PER_YEAR / vault_duration_days / tvl_usd * 100.0
            gross_yield_apr = gross_fee_income_usd * annualisation
            net_yield_apr = net_yield_usd * annualisation
        else:
            gross_yield_apr = 0.0
            net_yield_apr = 0.0

        pricing_error_bps = (net_yield_apr - fixed_apr) * 100.0

        if gross_fee_income_usd > 0:
            lvr_as_pct_of_gross_fees = total_lvr_usd / gross_fee_income_usd * 100.0
        else:
            lvr_as_pct_of_gross_fees = 0.0

        if fixed_apr != 0:
            variable_side_pnl_pct = (net_yield_apr - fixed_apr) / fixed_apr * 100.0
        else:
            variable_side_pnl_pct = 0.0

        return {
            "total_swaps": total_swaps,
            "in_range_swaps": in_range_swaps,
            "out_of_range_swaps": out_of_range_swaps,
            "time_in_range_pct": time_in_range_pct,
            "gross_fee_income_usd": gross_fee_income_usd,
            "fee_income_missed_out_of_range_usd": fee_income_missed_out_of_range_usd,
            "total_lvr_usd": total_lvr_usd,
            "net_yield_usd": net_yield_usd,
            "gross_yield_apr": gross_yield_apr,
            "net_yield_apr": net_yield_apr,
            "fixed_apr_offered": fixed_apr,
            "pricing_error_bps": pricing_error_bps,
            "lvr_as_pct_of_gross_fees": lvr_as_pct_of_gross_fees,
            "variable_side_pnl_pct": variable_side_pnl_pct,
        }
