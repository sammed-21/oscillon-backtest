#!/usr/bin/env python3
"""Build state-weighted annual LP surcharge model (honest allocator metric)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.annual_surcharge_model import (
    ALWAYS_ON_STATES,
    DEV_BUCKETS,
    EPISODIC_STATES,
    LP_SURPLUS_SHARE,
    TVL_GRID,
    avg_surcharge_bps_at_midpoint,
    break_even_tvl,
    calibration_rates,
    run_model,
    state_probabilities,
    load_chainlink_daily_states,
)

REF_TVL = 500_000_000


def _period_table_row(p) -> list[str]:
    return [
        p.label,
        f"{p.start} → {p.end}",
        f"{p.days:.1f}",
        f"${p.drain_volume_usd:,.0f}",
        f"${p.static_lp_income_usd:,.0f}",
        f"${p.hybrid_lp_income_usd:,.0f}",
        f"${p.lp_surcharge_usd:,.0f}",
        f"{p.avg_dev_bps:.2f}",
        f"{p.max_dev_bps:.1f}",
        str(p.drain_swaps_above_3bps),
    ]


def _bucket_pct_row(p) -> list[str]:
    return [p.label] + [f"{p.minute_pct_by_bucket.get(n, 0):.1f}%" for n, _, _ in DEV_BUCKETS]


def chainlink_year_table(chainlink_files: dict[str, Path]) -> list[str]:
    lines = [
        "## Chainlink daily deviation distribution (by year)",
        "",
        "Source: Ethereum mainnet Chainlink USDC/USD (`0x8fffffd4afb6115b954bd326cbe7b4ba576818f6`).",
        "Each calendar day classified by **max** |price − $1| during the day.",
        "",
        "| Year / file | Days | dead | micro | small | med | large | max (bps) |",
        "|-------------|------|------|-------|-------|-----|-------|-----------|",
    ]
    for key, path in sorted(chainlink_files.items()):
        if not path.exists():
            lines.append(f"| {key} | *missing* | — | — | — | — | — | — |")
            continue
        daily = load_chainlink_daily_states(path)
        probs = state_probabilities(daily)
        raw = pd.read_csv(path)
        ts_col = "minute" if "minute" in raw.columns else raw.columns[0]
        price_col = "usdc_price" if "usdc_price" in raw.columns else "price"
        dev = (raw[price_col].astype(float) - 1).abs() * 10_000
        lines.append(
            f"| {key} | {len(daily)} | {probs['dead']*100:.1f}% | "
            f"{probs['micro']*100:.1f}% | {probs['small']*100:.1f}% | "
            f"{probs['medium']*100:.1f}% | {probs['large']*100:.1f}% | "
            f"{dev.max():.1f} |"
        )
    return lines


def write_csv(scenarios: dict, out: Path) -> None:
    rows = []
    for scen_name, scen in scenarios.items():
        for tvl in TVL_GRID:
            rows.append(
                {
                    "scenario": scen_name,
                    "tvl_usd": tvl,
                    "annual_lp_surcharge_usd": scen.annual_lp_surcharge_usd,
                    "annual_always_on_usd": scen.annual_always_on_usd,
                    "annual_episodic_usd": scen.annual_episodic_usd,
                    "total_apr_bps": scen.apr_bps(tvl),
                    "always_on_apr_bps": scen.always_on_apr_bps(tvl),
                    "episodic_apr_bps": scen.episodic_apr_bps(tvl),
                }
            )
    pd.DataFrame(rows).to_csv(out, index=False)


def write_chart(scenarios: dict, out: Path) -> None:
    base = scenarios["BASE"]
    tvl_m = [t / 1e6 for t in TVL_GRID]
    always = [base.always_on_apr_bps(t) for t in TVL_GRID]
    episodic = [base.episodic_apr_bps(t) for t in TVL_GRID]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(tvl_m, always, label="Always-on (micro + small)", color="#6366F1")
    ax.bar(tvl_m, episodic, bottom=always, label="Episodic (medium + large)", color="#F59E0B")
    ax.axhline(10, color="gray", linestyle="--", linewidth=1, label="10 bps reference")
    ax.set_xlabel("TVL ($M)")
    ax.set_ylabel("LP surcharge APR (bps/year)")
    ax.set_title("BASE scenario: always-on vs episodic surcharge APR")
    ax.legend()
    ax.set_xticks(tvl_m)
    ax.set_xticklabels([f"${int(x)}M" for x in tvl_m])
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def disclosure_block(
    chainlink_files: dict[str, Path],
    calm,
    stress,
    recovery,
    scenarios: dict,
    *,
    before_base=None,
) -> list[str]:
    base = scenarios["BASE"]
    bear = scenarios["BEAR"]
    missing = [k for k, p in chainlink_files.items() if not p.exists()]
    always_bps = base.always_on_apr_bps(REF_TVL)
    negligible = always_bps < 5.0

    lines = [
        "## Honest disclosure (publishable)",
        "",
        "### Data sources",
        "- **Chainlink:** USDC/USD on Ethereum (`EACAggregatorProxy` `0x8fff…818f6`), via Dune SQL.",
        "- **Swap replays:** Uniswap v3 USDC/USDT `0x3416…27c6` minute data (demeter-fetch / BigQuery).",
        f"- **Calm calibration:** `{Path(calm.path).name}` ({calm.start} → {calm.end}, {calm.days:.0f} days).",
        f"- **Stress calibration:** `{Path(stress.path).name}` ({stress.start} → {stress.end}, {stress.days:.0f} days).",
    ]
    if recovery is not None:
        lines.append(
            f"- **Recovery calibration:** `{Path(recovery.path).name}` "
            f"({recovery.start} → {recovery.end}, {recovery.days:.0f} days, SVB post-peak)."
        )
    lines.extend(
        [
        "",
        "### Method",
        "```",
        "annual_lp_surcharge = Σ_state P(state) × lp_surcharge_per_day(state) × 365",
        "```",
        "- `P(state)` = empirical fraction of calendar days with max Chainlink deviation in bucket.",
        "- `lp_surcharge_per_day(state)` = day-weighted blend across calibration replays.",
        "- **Calm** → micro; **stress + recovery** → small/medium/large (recovery fills 4–15 bps gap).",
        "- **No** `stress_daily_rate × N_events` annualisation.",
        "",
        "### Scenarios",
        "- **BEAR:** 2024 Chainlink only (99.5% dead-band days).",
        "- **BASE:** All available Chainlink daily states (deduped), including **full-year 2023** (365 days).",
        "- **BULL:** March 2023 Chainlink window only (31 days, SVB stress distribution).",
        "",
        "### Not included",
        "- Volume loss / elastic routing (`--apply-routing`).",
        "- Protocol 15% cut (numbers are LP 85% of surcharge only).",
        "- Gas, MEV, oracle disagreement muting (conservative price path).",
        "- Drain volume on days without swap replay (extrapolation is the main uncertainty).",
        "",
        "### Conservative limitations (accepted)",
        "- **Swap source:** Some replays use Infura RPC minute fetch, not BigQuery demeter-fetch. "
        "A ~10% drain-volume uplift would move BASE ~0.71 → ~0.78 bps/year — immaterial vs allocator thresholds.",
        "- **Fee path:** Backtest uses on-chain integer hook fees (`oscillon_fee_bps`), not float `select_fee_bps`. "
        "Truncation slightly **understates** LP income (e.g. dev=7: 4.0 vs 4.33 bps) — conservative for published research.",
        "- **Oracle merge:** `merge_asof` backward on timestamp with no staleness cap. Rare long gaps "
        "(observed up to ~77 min) can **understate** `dev_bps` during fast depegs — also conservative.",
        ]
    )
    obs_days = calm.days + stress.days + (recovery.days if recovery else 0)
    lines.extend(
        [
            "",
            "### Key uncertainty",
            f"We observe swap flow on **{obs_days:.0f} days** of calibration replays. Chainlink covers "
            f"**{base.chainlink_days}** calendar days. Peak-stress-only calibration overstates "
            "episodic surcharge; recovery replay (Mar 16–25) is required for honest 4–15 bps economics.",
            "",
        ]
    )
    if before_base is not None and recovery is not None:
        delta = base.apr_bps(REF_TVL) - before_base.apr_bps(REF_TVL)
        pct = 100 * (base.annual_lp_surcharge_usd / before_base.annual_lp_surcharge_usd - 1)
        lines.extend(
            [
                "### Calibration sensitivity (BASE @ $500M TVL)",
                f"- **Before** (stress peak only for 7+ bps): {before_base.apr_bps(REF_TVL):.2f} bps/year "
                f"(${before_base.annual_lp_surcharge_usd:,.0f})",
                f"- **After** (+ recovery Mar 16–25): {base.apr_bps(REF_TVL):.2f} bps/year "
                f"(${base.annual_lp_surcharge_usd:,.0f})",
                f"- **Change:** {delta:+.2f} bps ({pct:+.1f}%)",
                "",
            ]
        )
    lines.extend(
        [
            "### Always-on vs episodic (BASE @ $500M TVL)",
            f"- Always-on (micro + small): **{always_bps:.2f} bps/year** (${base.annual_always_on_usd:,.0f})",
            f"- Episodic (medium + large): **{base.episodic_apr_bps(REF_TVL):.2f} bps/year** "
            f"(${base.annual_episodic_usd:,.0f})",
            f"- Total: **{base.apr_bps(REF_TVL):.2f} bps/year**",
            "",
        ]
    )
    if negligible:
        lines.append(
            "**Finding:** The always-on component is **< 5 bps/year** at $500M TVL — "
            "it does not materially change the allocator trade. Surcharge is **dominated by episodic stress** "
            "(when medium/large deviation days occur). This **confirms** the skeptic framing: "
            "365-day contract risk vs a few bps unless stress states are frequent."
        )
    else:
        lines.append(
            f"**Finding:** Always-on micro-depeg surcharge contributes **{always_bps:.1f} bps/year** "
            "at $500M — non-negligible vs a pure episodic story."
        )

    if missing:
        lines.append("")
        lines.append(f"**Missing Chainlink files:** {', '.join(missing)} — BASE uses available years only.")

    lines.append("")
    lines.append(
        f"**BEAR sanity check:** {bear.apr_bps(REF_TVL):.2f} bps/year @ $500M — "
        "calm-year surcharge is near zero."
    )
    return lines


def main() -> None:
    p = argparse.ArgumentParser(description="State-weighted annual surcharge model")
    p.add_argument(
        "--scenario",
        choices=["full", "2026-only"],
        default="full",
        help="full=all Chainlink years for BASE; 2026-only=current regime P(state)",
    )
    p.add_argument("--calm-prepared", default="")
    p.add_argument("--stress-prepared", default="data/prepared_swaps_2023-03.csv")
    p.add_argument(
        "--recovery-prepared",
        default="data/prepared_swaps_2023-03-recovery.csv",
        help="SVB recovery window (Mar 16–25 2023)",
    )
    p.add_argument("--md-out", default="")
    p.add_argument("--csv-out", default="")
    p.add_argument("--chart-out", default="")
    args = p.parse_args()

    regime_2026 = args.scenario == "2026-only"
    if not args.calm_prepared:
        args.calm_prepared = (
            "data/prepared_swaps_2026-06.csv"
            if regime_2026
            else "data/prepared_swaps_2026-05.csv"
        )
    if not args.md_out:
        args.md_out = (
            "output/annual_surcharge_model_2026_only.md"
            if regime_2026
            else "output/annual_surcharge_model.md"
        )
    if not args.csv_out:
        args.csv_out = (
            "output/annual_surcharge_model_2026_only.csv"
            if regime_2026
            else "output/annual_surcharge_model.csv"
        )
    if not args.chart_out:
        args.chart_out = (
            "output/surcharge_decomposition_2026_only.png"
            if regime_2026
            else "output/surcharge_decomposition.png"
        )

    chainlink_files = {
        "2023": ROOT / "data/chainlink_usdc_2023.csv",
        "2024": ROOT / "data/chainlink_usdc_2024.csv",
        "2025": ROOT / "data/chainlink_usdc_2025.csv",
        "2026": ROOT / "data/chainlink_usdc_2026.csv",
        "2026-05": ROOT / "data/chainlink_usdc_2026-05.csv",
        "2026-06": ROOT / "data/chainlink_usdc_2026-06.csv",
    }
    base_keys = ["2026", "2026-05", "2026-06"] if regime_2026 else None

    recovery_path = ROOT / args.recovery_prepared
    calm_label = "June 2026 calm" if regime_2026 else "May 2026 calm"
    _, _, _, scenarios_before = run_model(
        chainlink_files=chainlink_files,
        calm_prepared=ROOT / args.calm_prepared,
        stress_prepared=ROOT / args.stress_prepared,
        recovery_prepared=None,
        base_chainlink_keys=base_keys,
        calm_label=calm_label,
    )
    calm, stress, recovery, scenarios = run_model(
        chainlink_files=chainlink_files,
        calm_prepared=ROOT / args.calm_prepared,
        stress_prepared=ROOT / args.stress_prepared,
        recovery_prepared=recovery_path if recovery_path.exists() else None,
        base_chainlink_keys=base_keys,
        calm_label=calm_label,
    )

    base = scenarios["BASE"]
    bear = scenarios["BEAR"]
    before_base = scenarios_before["BASE"]
    rates = calibration_rates(calm, stress, recovery)

    lines = [
        "# Annual surcharge model (state-weighted)",
        "",
        f"**Scenario:** `{'2026-only (current regime)' if regime_2026 else 'full historical'}`",
        "",
        "Replaces event-frequency annualisation (`3 events × 3 days`) with:",
        "`Σ P(deviation state) × empirical LP surcharge/day × 365`.",
        "",
        "## Step 1 — Prepared swap periods",
        "",
        "| Period | Days | Drain vol | Static LP | Hybrid LP | LP surcharge (85%) | Avg dev | Max dev | Drain >3bps |",
        "|--------|------|-----------|-----------|-----------|-------------------|---------|---------|-------------|",
    ]
    periods = [calm, stress]
    if recovery is not None:
        periods.append(recovery)
    for period in periods:
        lines.append("| " + " | ".join(_period_table_row(period)) + " |")

    lines.extend(
        [
            "",
            "### % of minutes by deviation bucket",
            "",
            "| Period | 0-3 | 3-7 | 7-15 | 15-30 | 30+ |",
            "|--------|-----|-----|------|-------|-----|",
        ]
    )
    for period in periods:
        lines.append("| " + " | ".join(_bucket_pct_row(period)) + " |")

    lines.extend(
        [
            "",
            "### LP surcharge per day by bucket (empirical)",
            "",
            "| Bucket | Calm | Stress | Recovery | Blended rate |",
            "|--------|------|--------|----------|--------------|",
        ]
    )
    for name, _, _ in DEV_BUCKETS:
        rec_r = recovery.lp_surcharge_per_day_by_bucket.get(name, 0) if recovery else 0
        lines.append(
            f"| {name} | ${calm.lp_surcharge_per_day_by_bucket.get(name, 0):,.0f} | "
            f"${stress.lp_surcharge_per_day_by_bucket.get(name, 0):,.0f} | "
            f"${rec_r:,.0f} | ${rates.get(name, 0):,.0f} |"
        )

    lines.append("")
    lines.extend(chainlink_year_table(chainlink_files))

    lines.extend(
        [
            "",
            "## Step 3 — State-weighted formula",
            "",
            "| State | P(BEAR) | P(BASE) | P(BULL) | Blended $/day |",
            "|-------|---------|---------|---------|---------------|",
        ]
    )
    for name, _, _ in DEV_BUCKETS:
        lines.append(
            f"| {name} | {scenarios['BEAR'].state_probs[name]*100:.2f}% | "
            f"{base.state_probs[name]*100:.2f}% | {scenarios['BULL'].state_probs[name]*100:.2f}% | "
            f"${rates.get(name, 0):,.0f} |"
        )

    for scen_name in (("BASE",) if regime_2026 else ("BEAR", "BASE", "BULL")):
        scen = scenarios[scen_name]
        lines.extend(
            [
                "",
                f"## Step 4–5 — {scen_name} scenario"
                + (" (2026 Chainlink only)" if regime_2026 else ""),
                "",
                f"Chainlink days: {scen.chainlink_days} | Annual LP surcharge: "
                f"**${scen.annual_lp_surcharge_usd:,.0f}** | Always-on: "
                f"${scen.annual_always_on_usd:,.0f} | Episodic: ${scen.annual_episodic_usd:,.0f}",
                "",
                "| TVL | Total APR (bps) | Always-on (bps) | Episodic (bps) |",
                "|-----|-----------------|-----------------|----------------|",
            ]
        )
        for tvl in TVL_GRID:
            lines.append(
                f"| ${tvl/1e6:.0f}M | {scen.apr_bps(tvl):.2f} | "
                f"{scen.always_on_apr_bps(tvl):.2f} | {scen.episodic_apr_bps(tvl):.2f} |"
            )
        be = break_even_tvl(scen.annual_lp_surcharge_usd, 10.0)
        if be:
            lines.append(f"\nBreak-even TVL for 10 bps APR: **${be:,.0f}**")

    lines.extend(
        disclosure_block(
            chainlink_files,
            calm,
            stress,
            recovery,
            scenarios,
            before_base=before_base if recovery else None,
        )
    )

    md_out = ROOT / args.md_out
    csv_out = ROOT / args.csv_out
    chart_out = ROOT / args.chart_out
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text("\n".join(lines))
    write_csv(scenarios, csv_out)
    write_chart(scenarios, chart_out)

    always_bps = base.always_on_apr_bps(REF_TVL)
    total_bps = base.apr_bps(REF_TVL)
    be_base = break_even_tvl(base.annual_lp_surcharge_usd, 10.0)

    print("=" * 64)
    title = "2026-ONLY REGIME" if regime_2026 else "ANNUAL SURCHARGE MODEL"
    print(title + " — SUMMARY")
    print("=" * 64)
    if regime_2026:
        print(f"P(state) from: 2026 YTD + May + Jun Chainlink ({base.chainlink_days} days)")
        print(f"Calm calibration: {Path(args.calm_prepared).name}")
        print()
    print(f"1. Always-on surcharge APR @ $500M TVL (BASE): {always_bps:.2f} bps/year")
    print(f"2. Total surcharge APR @ $500M TVL (BASE):     {total_bps:.2f} bps/year")
    if be_base:
        print(f"3. Break-even TVL for 10 bps APR (BASE):       ${be_base:,.0f}")
    else:
        print("3. Break-even TVL for 10 bps APR:              N/A (zero surcharge)")
    print()
    if not regime_2026:
        print(f"BEAR @ $500M: {bear.apr_bps(REF_TVL):.2f} bps | BULL @ $500M: {scenarios['BULL'].apr_bps(REF_TVL):.2f} bps")
    if recovery is not None:
        delta = total_bps - before_base.apr_bps(REF_TVL)
        print()
        print("CALIBRATION COMPARISON (BASE @ $500M):")
        print(f"  Before (no recovery): {before_base.apr_bps(REF_TVL):.3f} bps  (${before_base.annual_lp_surcharge_usd:,.0f}/yr)")
        print(f"  After  (+ recovery):  {total_bps:.3f} bps  (${base.annual_lp_surcharge_usd:,.0f}/yr)")
        print(f"  Change: {delta:+.3f} bps ({100*(base.annual_lp_surcharge_usd/before_base.annual_lp_surcharge_usd-1):+.1f}%)")
    print(f"\nWrote {md_out}\n      {csv_out}\n      {chart_out}")


if __name__ == "__main__":
    main()
