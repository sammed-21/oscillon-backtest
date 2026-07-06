#!/usr/bin/env python3
"""Annual surcharge revenue ÷ TVL — the allocator metric."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.oscillon_fee import BASE_FEE_BPS, drain_surcharge_bps, select_fee_pips, FeeContext, fee_bps
from src.swap_direction import validate_prepared_swaps

LP_SURPLUS_SHARE = 0.85
PROTOCOL_SURPLUS_SHARE = 0.15


def analyze_prepared(
    path: Path,
    *,
    tvl: float,
    period_days: float | None = None,
) -> dict:
    swaps = pd.read_csv(path)
    swaps, _ = validate_prepared_swaps(swaps)
    if period_days is None:
        ts = pd.to_datetime(swaps["timestamp"])
        period_days = max((ts.max() - ts.min()).total_seconds() / 86400.0, 1.0)

    base_income = 0.0
    total_fee_income = 0.0
    surcharge_gross = 0.0
    drain_notional = 0.0
    drain_minutes = 0
    stress_drain_minutes = 0  # dev >= 3

    for _, row in swaps.iterrows():
        dev = float(row["dev_bps"])
        drain = bool(row["is_drain"])
        drain_size = float(row.get("drain_size_usd", 0))
        restore_size = float(row.get("restore_size_usd", 0))
        total_size = drain_size + restore_size

        base_income += total_size * BASE_FEE_BPS / 10_000

        if not drain:
            total_fee_income += total_size * BASE_FEE_BPS / 10_000
            continue

        drain_minutes += 1
        if dev >= 3:
            stress_drain_minutes += 1

        surcharge_bps = drain_surcharge_bps(dev) if dev >= 3 else 0.0
        ctx = FeeContext(int(dev), True, fee_model="hybrid", k_override=45)
        total_fee_bps = fee_bps(select_fee_pips(ctx))

        drain_notional += drain_size
        surcharge_gross += drain_size * surcharge_bps / 10_000
        total_fee_income += (
            drain_size * total_fee_bps / 10_000 + restore_size * BASE_FEE_BPS / 10_000
        )

    extra_total_fee = total_fee_income - base_income  # ≈ hybrid - static uplift
    lp_surcharge = surcharge_gross * LP_SURPLUS_SHARE
    protocol_surcharge = surcharge_gross * PROTOCOL_SURPLUS_SHARE

    per_day_lp_surcharge = lp_surcharge / period_days

    def annualise(events: int, days_each: int) -> float:
        return events * days_each * per_day_lp_surcharge

    scenarios = {
        "1_event_6d": annualise(1, int(round(period_days))),
        "3_events_3d": annualise(3, 3),
        "4_events_4d": annualise(4, 4),
        "naive_365d": lp_surcharge * (365 / period_days),
    }

    def bps_apr(annual_usd: float) -> float:
        return (annual_usd / tvl) * 10_000 if tvl > 0 else 0.0

    return {
        "path": str(path),
        "period_days": period_days,
        "tvl": tvl,
        "drain_minutes": drain_minutes,
        "stress_drain_minutes": stress_drain_minutes,
        "drain_notional_usd": drain_notional,
        "base_fee_income_usd": base_income,
        "total_hybrid_fee_income_usd": total_fee_income,
        "extra_fee_vs_base_usd": extra_total_fee,
        "surcharge_gross_usd": surcharge_gross,
        "lp_surcharge_85pct_usd": lp_surcharge,
        "protocol_surcharge_15pct_usd": protocol_surcharge,
        "lp_surcharge_per_day_usd": per_day_lp_surcharge,
        "scenarios": {k: {"annual_usd": v, "apr_bps": bps_apr(v)} for k, v in scenarios.items()},
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Surcharge-only APR analysis")
    p.add_argument("--prepared", default="data/prepared_swaps_2023-03.csv")
    p.add_argument("--tvl", type=float, default=15_000_000)
    p.add_argument("--out", default="output/surcharge_apr_analysis.md")
    args = p.parse_args()

    r = analyze_prepared(Path(args.prepared), tvl=args.tvl)
    s = r["scenarios"]

    lines = [
        "# Surcharge-only APR analysis",
        "",
        f"**Source:** `{r['path']}`  ",
        f"**Stress window:** {r['period_days']:.1f} days  ",
        f"**TVL assumption:** ${r['tvl']:,.0f}",
        "",
        "## What this measures",
        "",
        "Only the **depeg surcharge** (fee above 3 bps base) on **drain-direction** swaps.",
        "LP receives **85%** of surcharge (on-chain surplus split); protocol 15%.",
        "",
        "Static 3 bps base is **not** included — both static and Oscillon earn that.",
        "",
        "## March 2023 stress window (actual replay)",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Drain minutes | {r['drain_minutes']:,} ({r['stress_drain_minutes']:,} with dev ≥ 3 bps) |",
        f"| Drain notional | ${r['drain_notional_usd']:,.0f} |",
        f"| Surcharge gross (100%) | ${r['surcharge_gross_usd']:,.0f} |",
        f"| **LP surcharge (85%)** | **${r['lp_surcharge_85pct_usd']:,.0f}** |",
        f"| LP surcharge / day | ${r['lp_surcharge_per_day_usd']:,.0f} |",
        f"| Extra total fee vs base (incl. surcharge) | ${r['extra_fee_vs_base_usd']:,.0f} |",
        "",
        "## Annual surcharge ÷ TVL (event-frequency scenarios)",
        "",
        "| Scenario | Annual LP surcharge | APR (bps) |",
        "|----------|---------------------|-----------|",
    ]
    labels = {
        "1_event_6d": "1 stress event / year (6 days, same severity)",
        "3_events_3d": "3 events × 3 days (conservative)",
        "4_events_4d": "4 events × 4 days",
        "naive_365d": "Naive 365/6 annualisation (WRONG — do not publish)",
    }
    for key, label in labels.items():
        lines.append(
            f"| {label} | ${s[key]['annual_usd']:,.0f} | **{s[key]['apr_bps']:.1f} bps** |"
        )

    annual_3x3 = s["3_events_3d"]["annual_usd"]
    tvl_grid = [
        15_000_000,
        50_000_000,
        100_000_000,
        250_000_000,
        500_000_000,
        1_000_000_000,
    ]
    lines.extend(
        [
            "",
            "## TVL sensitivity (3 events × 3 days / year)",
            "",
            "Surcharge USD is fixed by replay; APR scales inversely with TVL.",
            "",
            "| TVL | LP surcharge APR (bps/year) |",
            "|-----|------------------------------|",
        ]
    )
    for t in tvl_grid:
        bps = (annual_3x3 / t) * 10_000
        lines.append(f"| ${t / 1e6:.0f}M | **{bps:.1f} bps** |")

    ref_bps = (annual_3x3 / 500_000_000) * 10_000
    lines.extend(
        [
            "",
            "## Allocator verdict",
            "",
            f"At **$15M TVL** (research default): ~{s['3_events_3d']['apr_bps']:.0f} bps/year.",
            f"At **$500M TVL** (flagship pool): ~{ref_bps:.0f} bps/year — inside the 5–15 bps skeptic range.",
            "",
            "Tail risk (oracle hook) is 365 days; surcharge payout is ~9 stress-days/year.",
            "",
            "Oracle disagreement: `output/oracle_disagreement_audit.md`.",
            "",
            "Calm months (May 2026 replay): surcharge ≈ $0.",
            "",
        ]
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))

    print("=" * 60)
    print("SURCHARGE-ONLY APR (LP 85% of surcharge)")
    print("=" * 60)
    print(f"Period: {r['period_days']:.1f} days | TVL: ${r['tvl']:,.0f}")
    print(f"LP surcharge in window: ${r['lp_surcharge_85pct_usd']:,.0f}")
    print(f"Per day: ${r['lp_surcharge_per_day_usd']:,.0f}")
    print()
    for key, label in labels.items():
        print(f"  {label}")
        print(f"    Annual LP surcharge: ${s[key]['annual_usd']:,.0f}")
        print(f"    APR: {s[key]['apr_bps']:.1f} bps")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
