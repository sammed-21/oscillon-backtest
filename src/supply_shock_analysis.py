"""
Compare static vs Oscillon dynamic fees for supply-shock scenarios.

Produces side-by-side summaries against organic-arbitrage replay results
without modifying the organic scenario modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .oscillon_fee import BASE_FEE_BPS, select_fee_bps
from .swap_direction import validate_prepared_swaps
from .supply_shock_scenario import (
    FeeMode,
    SupplyShockResult,
    SupplyShockScenario,
    simulate_supply_shock,
    stabl_r_default,
)


@dataclass
class SupplyShockComparison:
    static: SupplyShockResult
    dynamic: SupplyShockResult

    @property
    def attacker_payoff_delta_usd(self) -> float:
        return self.dynamic.net_attacker_payoff_usd - self.static.net_attacker_payoff_usd

    @property
    def attacker_payoff_delta_pct(self) -> float:
        base = self.static.net_attacker_payoff_usd
        if base <= 0:
            return 0.0
        return (self.attacker_payoff_delta_usd / base) * 100.0

    @property
    def lp_loss_delta_usd(self) -> float:
        return self.dynamic.lp_net_loss_usd - self.static.lp_net_loss_usd

    @property
    def lp_loss_delta_pct(self) -> float:
        base = self.static.lp_net_loss_usd
        if base <= 0:
            return 0.0
        return (self.lp_loss_delta_usd / base) * 100.0


def run_supply_shock_comparison(
    scenario: SupplyShockScenario,
) -> SupplyShockComparison:
    static = simulate_supply_shock(scenario, fee_mode="static")
    dynamic = simulate_supply_shock(scenario, fee_mode="dynamic")
    return SupplyShockComparison(static=static, dynamic=dynamic)


@dataclass
class OrganicArbitrageSummary:
    """Headline metrics from historical swap replay (March 2023 stress window)."""

    label: str
    period_days: float
    static_lp_income_usd: float
    dynamic_lp_income_usd: float
    static_lvr_usd: float
    dynamic_lvr_usd: float
    static_lp_capture_pct: float
    dynamic_lp_capture_pct: float
    lp_income_improvement_pct: float
    lvr_reduction_pct: float


def _safe_dev_bps(dev_bps: float) -> float:
    if dev_bps is None or (isinstance(dev_bps, float) and dev_bps != dev_bps):
        return 0.0
    return float(dev_bps)


def summarize_organic_arbitrage_from_prepared(
    prepared_csv: str | Path,
    *,
    label: str = "Organic arbitrage (March 2023)",
) -> OrganicArbitrageSummary:
    """
    Replay prepared swap data with the same economics as backtest_mainnet.py
    (static vs Oscillon hybrid) without importing the chart script.
    """
    swaps = pd.read_csv(prepared_csv)
    swaps["timestamp"] = pd.to_datetime(swaps["timestamp"])
    swaps, _ = validate_prepared_swaps(swaps)
    ts = swaps["timestamp"]
    period_days = max((ts.max() - ts.min()).total_seconds() / 86400.0, 1.0)

    static_lp = static_lvr = dynamic_lp = dynamic_lvr = 0.0

    for _, swap in swaps.iterrows():
        dev = _safe_dev_bps(swap["dev_bps"])
        drain = bool(swap["is_drain"])
        drain_size = float(swap.get("drain_size_usd", swap.get("swap_size_usd", 0)))
        restore_size = float(swap.get("restore_size_usd", 0))
        total_size = drain_size + restore_size

        static_fee = BASE_FEE_BPS
        dynamic_fee = select_fee_bps(dev, drain, fee_model="hybrid", k_override=45)

        if drain:
            static_lp += drain_size * static_fee / 10_000 + restore_size * BASE_FEE_BPS / 10_000
            dynamic_lp += (
                drain_size * dynamic_fee / 10_000 + restore_size * BASE_FEE_BPS / 10_000
            )
            if dev > 0:
                static_lvr += max(0.0, drain_size * (dev - static_fee) / 10_000)
                dynamic_lvr += max(0.0, drain_size * (dev - dynamic_fee) / 10_000)
        else:
            static_lp += total_size * static_fee / 10_000
            dynamic_lp += total_size * dynamic_fee / 10_000

    static_total = static_lp + static_lvr
    dynamic_total = dynamic_lp + dynamic_lvr
    static_cap = (static_lp / static_total * 100) if static_total > 0 else 100.0
    dynamic_cap = (dynamic_lp / dynamic_total * 100) if dynamic_total > 0 else 100.0

    lp_improve = (
        ((dynamic_lp - static_lp) / static_lp) * 100 if static_lp > 0 else 0.0
    )
    lvr_reduce = (
        ((static_lvr - dynamic_lvr) / static_lvr) * 100 if static_lvr > 0 else 0.0
    )

    return OrganicArbitrageSummary(
        label=label,
        period_days=period_days,
        static_lp_income_usd=static_lp,
        dynamic_lp_income_usd=dynamic_lp,
        static_lvr_usd=static_lvr,
        dynamic_lvr_usd=dynamic_lvr,
        static_lp_capture_pct=static_cap,
        dynamic_lp_capture_pct=dynamic_cap,
        lp_income_improvement_pct=lp_improve,
        lvr_reduction_pct=lvr_reduce,
    )


def build_scenario_comparison_markdown(
    organic: OrganicArbitrageSummary,
    supply: SupplyShockComparison,
    *,
    supply_label: str = "Supply shock (StablR default)",
) -> str:
    """Markdown table contrasting organic-arbitrage vs supply-shock outcomes."""
    s = supply.static
    d = supply.dynamic

    def pct_delta(new: float, old: float) -> str:
        if old <= 0:
            return "n/a"
        return f"{((new - old) / old) * 100:+.2f}%"

    lines = [
        "# Oscillon scenario comparison: organic arbitrage vs supply shock",
        "",
        "Two structurally distinct depeg classes. Fee escalation is designed for",
        "capital-at-risk arbitrage flows, not zero-cost-basis mint-and-dump attacks.",
        "",
        "## Supply shock detail (StablR-class)",
        "",
        f"| Parameter | Value |",
        f"|-----------|-------|",
        f"| Mint face value | ${s.scenario.mint_face_value:,.0f} |",
        f"| Pool depth (per side) | ${s.scenario.pool_liquidity_depth:,.0f} |",
        f"| Dump blocks | {s.scenario.dump_blocks} |",
        f"| Initial oracle depeg | {s.scenario.initial_dev_bps:.1f} bps |",
        f"| Max fee-tier depeg used | {d.max_dev_bps:.1f} bps |",
        "",
        "## Headline comparison",
        "",
        "| Metric | Organic arbitrage | Supply shock (static fee) | Supply shock (Oscillon) | Escalation impact (supply) |",
        "|--------|-------------------|---------------------------|-------------------------|----------------------------|",
        f"| Scenario | {organic.label} | {supply_label} | {supply_label} | — |",
        f"| Attacker / arb extraction (USD) | LVR static ${organic.static_lvr_usd:,.0f} → dynamic ${organic.dynamic_lvr_usd:,.0f} | Net payoff ${s.net_attacker_payoff_usd:,.0f} | Net payoff ${d.net_attacker_payoff_usd:,.0f} | {pct_delta(d.net_attacker_payoff_usd, s.net_attacker_payoff_usd)} |",
        f"| LP fee income (USD) | ${organic.static_lp_income_usd:,.0f} → ${organic.dynamic_lp_income_usd:,.0f} | ${s.lp_fee_income_usd:,.0f} | ${d.lp_fee_income_usd:,.0f} | {pct_delta(d.lp_fee_income_usd, s.lp_fee_income_usd)} |",
        f"| LP net loss (USD) | LVR static ${organic.static_lvr_usd:,.0f} → dynamic ${organic.dynamic_lvr_usd:,.0f} | ${s.lp_net_loss_usd:,.0f} | ${d.lp_net_loss_usd:,.0f} | {pct_delta(d.lp_net_loss_usd, s.lp_net_loss_usd)} |",
        f"| LP capture / defense | {organic.static_lp_capture_pct:.1f}% → {organic.dynamic_lp_capture_pct:.1f}% (+{organic.lp_income_improvement_pct:.0f}% income, −{organic.lvr_reduction_pct:.0f}% LVR) | — | — | Attacker payoff Δ {supply.attacker_payoff_delta_pct:+.3f}% |",
        "",
        "## Interpretation",
        "",
        f"- **Organic arbitrage:** Oscillon raises LP income by **{organic.lp_income_improvement_pct:.1f}%** and cuts LVR by **{organic.lvr_reduction_pct:.1f}%** over {organic.period_days:.0f} days of historical stress replay.",
        f"- **Supply shock:** Fee escalation changes attacker net payoff by only **{supply.attacker_payoff_delta_pct:+.3f}%** (${supply.attacker_payoff_delta_usd:+,.0f}) and LP net loss by **{supply.lp_loss_delta_pct:+.3f}%** — dominated by slippage, not fees.",
        "- Slippage model: constant-product full-range pool; calibrate `pool_liquidity_depth` against observed realized proceeds.",
        "",
    ]
    return "\n".join(lines)


def supply_shock_summary_dict(comparison: SupplyShockComparison) -> dict[str, Any]:
    s, d = comparison.static, comparison.dynamic
    return {
        "static": {
            "gross_proceeds_usd": s.gross_proceeds_usd,
            "total_fees_paid_usd": s.total_fees_paid_usd,
            "net_attacker_payoff_usd": s.net_attacker_payoff_usd,
            "lp_fee_income_usd": s.lp_fee_income_usd,
            "lp_net_loss_usd": s.lp_net_loss_usd,
            "max_dev_bps": s.max_dev_bps,
        },
        "dynamic": {
            "gross_proceeds_usd": d.gross_proceeds_usd,
            "total_fees_paid_usd": d.total_fees_paid_usd,
            "net_attacker_payoff_usd": d.net_attacker_payoff_usd,
            "lp_fee_income_usd": d.lp_fee_income_usd,
            "lp_net_loss_usd": d.lp_net_loss_usd,
            "max_dev_bps": d.max_dev_bps,
        },
        "attacker_payoff_delta_pct": comparison.attacker_payoff_delta_pct,
        "lp_loss_delta_pct": comparison.lp_loss_delta_pct,
    }


def run_stabl_r_default_comparison(
    prepared_organic_csv: str | Path | None = None,
) -> tuple[SupplyShockComparison, OrganicArbitrageSummary | None, str]:
    scenario = stabl_r_default()
    comparison = run_supply_shock_comparison(scenario)

    organic: OrganicArbitrageSummary | None = None
    if prepared_organic_csv and Path(prepared_organic_csv).exists():
        organic = summarize_organic_arbitrage_from_prepared(prepared_organic_csv)

    if organic is None:
        markdown = _supply_only_markdown(comparison)
    else:
        markdown = build_scenario_comparison_markdown(organic, comparison)

    return comparison, organic, markdown


def _supply_only_markdown(comparison: SupplyShockComparison) -> str:
    s, d = comparison.static, comparison.dynamic
    return (
        f"# Supply shock scenario (StablR default)\n\n"
        f"| | Static {s.scenario.static_fee_bps:g} bps | Oscillon dynamic |\n"
        f"|---|---|---|\n"
        f"| Gross proceeds | ${s.gross_proceeds_usd:,.0f} | ${d.gross_proceeds_usd:,.0f} |\n"
        f"| Fees paid | ${s.total_fees_paid_usd:,.0f} | ${d.total_fees_paid_usd:,.0f} |\n"
        f"| Net attacker payoff | ${s.net_attacker_payoff_usd:,.0f} | ${d.net_attacker_payoff_usd:,.0f} |\n"
        f"| LP net loss | ${s.lp_net_loss_usd:,.0f} | ${d.lp_net_loss_usd:,.0f} |\n"
        f"| Attacker payoff Δ | — | {comparison.attacker_payoff_delta_pct:+.3f}% |\n"
    )
