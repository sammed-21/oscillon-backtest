#!/usr/bin/env python3
"""
Backtest models on prepared swap-level ETH mainnet data.

This is the direct implementation of the multi-model analysis flow:
static, Oscillon K=45, no-threshold, and paper-minimum variants.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest_engine import simulate_swap_row, summarize_backtest
from src.oscillon_fee import BASE_FEE_BPS, oscillon_fee_bps, select_fee_bps
from src.swap_direction import TOKEN0_SYMBOL, TOKEN1_SYMBOL, validate_prepared_swaps


def _safe_dev_bps(dev_bps: float) -> float:
    if dev_bps is None or (isinstance(dev_bps, float) and dev_bps != dev_bps):
        return 0.0
    return float(dev_bps)


def static_fee(dev_bps: float, is_drain: bool) -> float:
    return BASE_FEE_BPS


def oscillon_fee_k45(dev_bps: float, is_drain: bool) -> float:
    d = _safe_dev_bps(dev_bps)
    return oscillon_fee_bps(int(d), is_drain, k=45, fee_model="quadratic")


def oscillon_fee_piecewise(dev_bps: float, is_drain: bool) -> float:
    return select_fee_bps(_safe_dev_bps(dev_bps), is_drain, fee_model="piecewise")


def oscillon_fee_hybrid(dev_bps: float, is_drain: bool) -> float:
    # Hook integer path (matches on-chain); truncates vs float select_fee_bps — conservative LP income.
    return oscillon_fee_bps(int(_safe_dev_bps(dev_bps)), is_drain, k=45, fee_model="hybrid")


def oscillon_fee_additive(dev_bps: float, is_drain: bool) -> float:
    """BASE_FEE on every swap + hybrid drain tax on top when is_drain."""
    return select_fee_bps(_safe_dev_bps(dev_bps), is_drain, fee_model="additive", k_override=45)


def oscillon_fee_no_threshold(dev_bps: float, is_drain: bool) -> float:
    max_fee = 50.0
    k = 45
    if not is_drain:
        return BASE_FEE_BPS
    fee = BASE_FEE_BPS + (k * dev_bps * dev_bps) / 10000
    return min(fee, max_fee)


def paper_minimum_fee(dev_bps: float, is_drain: bool, safety: float = 1.05) -> float:
    """
    Academic reference only — NOT a deployable fee policy.

    Sets fee = max(dev × safety, quadratic) on drain swaps. Because safety > 1,
    fee often exceeds dev_bps, zeroing LVR and inflating LP income beyond the
    arb spread. Use only as an oracle ceiling benchmark, not vs Oscillon hybrid.
    """
    max_fee = 50.0
    if not is_drain:
        return BASE_FEE_BPS
    minimum = dev_bps * safety
    quadratic = BASE_FEE_BPS + (45 * dev_bps * dev_bps) / 10000
    return min(max(minimum, quadratic), max_fee)


def build_charts(
    all_results: dict[str, pd.DataFrame],
    chart_out: str,
    show_chart: bool,
) -> None:
    fig = plt.figure(figsize=(16, 12))
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.4, wspace=0.3)

    # Chart 1: Fee curve comparison
    ax1 = fig.add_subplot(gs[0, 0])
    dev_range = np.linspace(0, 50, 500)
    ax1.plot(
        dev_range,
        [static_fee(d, True) for d in dev_range],
        label=f"Static ({BASE_FEE_BPS:g} bps)",
        color="gray",
        linewidth=2,
        linestyle="--",
    )
    ax1.plot(
        dev_range,
        [oscillon_fee_hybrid(d, True) for d in dev_range],
        label="Oscillon hybrid",
        color="#4F5FD4",
        linewidth=2,
    )
    ax1.plot(
        dev_range,
        [oscillon_fee_additive(d, True) for d in dev_range],
        label=f"Additive ({BASE_FEE_BPS:g} bps + drain tax)",
        color="#10B981",
        linewidth=2,
        linestyle="-.",
    )
    ax1.plot(
        dev_range,
        [oscillon_fee_piecewise(d, True) for d in dev_range],
        label="Oscillon piecewise",
        color="#6366F1",
        linewidth=1.5,
        linestyle=":",
    )
    ax1.plot(
        dev_range,
        [oscillon_fee_k45(d, True) for d in dev_range],
        label="Oscillon quadratic K=45",
        color="#818CF8",
        linewidth=1.5,
        linestyle="--",
    )
    ax1.plot(
        dev_range,
        [paper_minimum_fee(d, True) for d in dev_range],
        label="Ref: dev×1.05 ceiling (not deployable)",
        color="#00A388",
        linewidth=2,
    )
    ax1.axvline(
        x=3, color="orange", linestyle=":", alpha=0.7, label="SMALL_DEPEG_BPS = 3"
    )
    ax1.set_xlabel("Oracle Deviation (bps)")
    ax1.set_ylabel("Fee (bps)")
    ax1.set_title("Fee Curves: What Each Model Charges")
    ax1.legend(fontsize=9)
    ax1.set_ylim(0, 25)
    ax1.grid(True, alpha=0.3)

    # Chart 2: LVR distribution (skip empty series — density=True divides by zero)
    ax2 = fig.add_subplot(gs[0, 1])
    static_lvr = all_results["Static (current)"]["lvr"]
    oscillon_lvr = all_results["Oscillon hybrid"]["lvr"]
    ref_key = "Ref: dev×1.05 ceiling"
    paper_lvr = all_results[ref_key]["lvr"] if ref_key in all_results else None
    lvr_series = [
        ("Static", static_lvr[static_lvr > 0], "gray"),
        ("Oscillon hybrid", oscillon_lvr[oscillon_lvr > 0], "#4F5FD4"),
    ]
    if paper_lvr is not None:
        lvr_series.append(("Ref dev×1.05", paper_lvr[paper_lvr > 0], "#00A388"))
    plotted = False
    for label, data, color in lvr_series:
        if len(data) == 0:
            continue
        ax2.hist(data, bins=50, alpha=0.5, label=label, color=color, density=True)
        plotted = True
    if not plotted:
        ax2.text(
            0.5,
            0.5,
            "No positive LVR in this period\n(calm regime — expected)",
            ha="center",
            va="center",
            transform=ax2.transAxes,
            fontsize=11,
            color="gray",
        )
    ax2.set_xlabel("LVR per Swap ($)")
    ax2.set_ylabel("Density")
    ax2.set_title("LVR Distribution: How Much Arb Extracts Per Swap")
    if plotted:
        ax2.legend(fontsize=9)
    ax2.set_xlim(0, 500)
    ax2.grid(True, alpha=0.3)

    # Chart 3: Cumulative LP income over time
    ax3 = fig.add_subplot(gs[1, 0])
    colors = {
        "Static (current)": "gray",
        "Oscillon hybrid": "#4F5FD4",
        "Oscillon piecewise (3-zone)": "#6366F1",
        "Oscillon K=45 (quadratic)": "#818CF8",
        "Oscillon no threshold": "#A5B4FC",
        "Ref: dev×1.05 ceiling": "#00A388",
    }
    for model_name, df in all_results.items():
        cum_income = df["lp_income"].cumsum()
        ax3.plot(
            range(len(cum_income)),
            cum_income,
            label=model_name,
            color=colors.get(model_name, "black"),
            linewidth=1.5,
        )
    ax3.set_xlabel("Swap Number (chronological)")
    ax3.set_ylabel("Cumulative LP Income ($)")
    ax3.set_title("Cumulative LP Income Over Time")
    ax3.legend(fontsize=8)
    ax3.grid(True, alpha=0.3)

    # Chart 4: LP capture % by market condition
    ax4 = fig.add_subplot(gs[1, 1])
    cond_labels = ["0-3 bps", "3-7 bps", "7-15 bps", "15-30 bps", "30+ bps"]
    cond_ranges = [(0, 3), (3, 7), (7, 15), (15, 30), (30, 999)]
    x = np.arange(len(cond_labels))

    ordered_models = [
        "Static (current)",
        "Oscillon hybrid",
        "Oscillon piecewise (3-zone)",
        "Oscillon K=45 (quadratic)",
    ]
    ordered_models = [m for m in ordered_models if m in all_results]
    width = 0.8 / max(len(ordered_models), 1)
    for i, model_name in enumerate(ordered_models):
        df = all_results[model_name]
        capture_by_cond = []
        for low, high in cond_ranges:
            mask = (df["dev_bps"] >= low) & (df["dev_bps"] < high)
            subset = df[mask]
            lp_inc = subset["lp_income"].sum()
            lvr = subset["lvr"].sum()
            total = lp_inc + lvr
            cap = (lp_inc / total * 100) if total > 0 else 100.0
            capture_by_cond.append(cap)
        ax4.bar(
            x + (i * width),
            capture_by_cond,
            width,
            label=model_name,
            color=colors.get(model_name, "black"),
            alpha=0.8,
        )

    ax4.set_xlabel("Market Condition (deviation range)")
    ax4.set_ylabel("LP Value Capture (%)")
    ax4.set_title("LP Capture % by Market Condition")
    ax4.set_xticks(x + width * (len(ordered_models) - 1) / 2)
    ax4.set_xticklabels(cond_labels, fontsize=9)
    ax4.legend(fontsize=8)
    ax4.axhline(y=50, color="red", linestyle="--", alpha=0.5)
    ax4.set_ylim(0, 105)
    ax4.grid(True, alpha=0.3, axis="y")

    plt.suptitle(
        "Oscillon Fee Mechanism Backtest\nUSDC/USDT Pool - Historical Data",
        fontsize=14,
        fontweight="bold",
    )
    plt.savefig(chart_out, dpi=150, bbox_inches="tight")
    print(f"Chart saved: {chart_out}")
    if show_chart:
        plt.show()
    plt.close(fig)


def build_timeline_chart(
    timeline: pd.DataFrame,
    chart_out: str,
    show_chart: bool,
) -> None:
    """Depeg and fee (bps) vs timestamp — minute-level from prepared data."""
    ts = pd.to_datetime(timeline["timestamp"])
    dev = timeline["dev_bps"]

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    axes[0].plot(ts, dev, color="#E85D04", linewidth=0.8, alpha=0.9)
    axes[0].axhline(7, color="orange", linestyle=":", alpha=0.6, label="7 bps threshold")
    axes[0].set_ylabel("Oracle depeg (bps)")
    axes[0].set_title("USDC depeg over time (Chainlink, matched per minute)")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(
        ts,
        timeline["fee_static_bps"],
        label=f"Static {BASE_FEE_BPS:g} bps",
        color="gray",
        linewidth=1.0,
        alpha=0.85,
    )
    axes[1].plot(
        ts,
        timeline["fee_oscillon_bps"],
        label="Oscillon hybrid",
        color="#4F5FD4",
        linewidth=1.0,
        alpha=0.85,
    )
    axes[1].set_ylabel("Fee (bps)")
    axes[1].set_title("Fee charged at each minute (static vs Oscillon)")
    axes[1].legend(loc="upper right", fontsize=8)
    axes[1].grid(True, alpha=0.3)

    gap = (dev - timeline["fee_oscillon_bps"]).clip(lower=0)
    axes[2].fill_between(ts, 0, gap, color="#FF6B6B", alpha=0.35, label="Depeg − Oscillon fee")
    axes[2].set_ylabel("Gap (bps)")
    axes[2].set_xlabel("Time (UTC)")
    axes[2].set_title("Remaining arb gap after Oscillon fee (drain protection proxy)")
    axes[2].legend(loc="upper right", fontsize=8)
    axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(chart_out, dpi=150, bbox_inches="tight")
    print(f"Timeline chart saved: {chart_out}")
    if show_chart:
        plt.show()
    plt.close(fig)


def build_timeline_from_swaps(swaps: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for _, s in swaps.iterrows():
        dev = _safe_dev_bps(s["dev_bps"])
        drain = bool(s["is_drain"])
        rows.append(
            {
                "timestamp": s["timestamp"],
                "oracle_price": float(s.get("oracle_price", 1.0)),
                "pool_price": float(s.get("pool_price", 1.0)),
                "dev_bps": dev,
                "is_drain": drain,
                "fee_static_bps": static_fee(dev, drain),
                "fee_oscillon_bps": oscillon_fee_hybrid(dev, drain),
                "fee_piecewise_bps": oscillon_fee_piecewise(dev, drain),
                "fee_quadratic_bps": oscillon_fee_k45(dev, drain),
                "fee_paper_bps": paper_minimum_fee(dev, drain),
                "swap_size_usd": float(s.get("swap_size_usd", 0)),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Run swap-level mainnet fee/LVR backtest")
    p.add_argument("--prepared", default="data/prepared_swaps.csv")
    p.add_argument("--curve-fee-bps", type=float, default=4.0)
    p.add_argument("--tvl", type=float, default=15_000_000)
    p.add_argument("--chart-out", default="output/oscillon_backtest_results.png")
    p.add_argument(
        "--timeline-out",
        default="output/depeg_fee_timeline.png",
        help="Time-series chart: depeg + fee by timestamp",
    )
    p.add_argument(
        "--timeline-csv",
        default="output/depeg_fee_timeline.csv",
        help="CSV export of per-minute depeg and fees",
    )
    p.add_argument("--show-chart", action="store_true")
    p.add_argument("--timeline-only", action="store_true", help="Only build timeline chart/CSV")
    p.add_argument(
        "--apply-routing",
        action="store_true",
        help="Scale drain volume by elastic routing model when fee > competitor (default: off)",
    )
    p.add_argument("--volume-eta", type=float, default=2.0, help="Routing elasticity if --apply-routing")
    args = p.parse_args()

    swaps = pd.read_csv(args.prepared)
    swaps["timestamp"] = pd.to_datetime(swaps["timestamp"])
    swaps, mismatches = validate_prepared_swaps(swaps)

    oracle_leg = str(swaps["oracle_leg"].iloc[0]) if "oracle_leg" in swaps.columns else "token0"
    oracle_asset = (
        str(swaps["oracle_asset"].iloc[0])
        if "oracle_asset" in swaps.columns
        else ("USDC" if oracle_leg == "token0" else "USDT")
    )
    drain_net_col = "netAmount1" if oracle_leg == "token1" else "netAmount0"

    if mismatches:
        print(
            f"WARNING: fixed {mismatches:,} is_drain rows "
            f"(oracle_leg={oracle_leg}: peg_below & {oracle_asset} in via {drain_net_col})"
        )
    ts = pd.to_datetime(swaps["timestamp"])
    period_days = max((ts.max() - ts.min()).total_seconds() / 86400.0, 1.0)
    print(f"Oracle leg: {oracle_leg} ({oracle_asset}/USD Chainlink — no mixed feeds)")
    print(f"Pool tokens: token0={TOKEN0_SYMBOL}, token1={TOKEN1_SYMBOL}")
    print(f"Backtest period: {period_days:.1f} days")
    drain_vol_col = "drain_size_usd" if "drain_size_usd" in swaps.columns else "swap_size_usd"
    print(
        f"Drain swaps: {swaps['is_drain'].sum():,} / {len(swaps):,} "
        f"(${swaps.loc[swaps['is_drain'], drain_vol_col].sum():,.0f} drain volume)"
    )

    timeline = build_timeline_from_swaps(swaps)
    Path(args.timeline_csv).parent.mkdir(parents=True, exist_ok=True)
    timeline.to_csv(args.timeline_csv, index=False)
    print(f"Timeline data saved: {args.timeline_csv} ({len(timeline):,} rows)")
    build_timeline_chart(timeline, chart_out=args.timeline_out, show_chart=args.show_chart)

    if oracle_leg == "token1":
        print(
            "\nNOTE: USDT oracle leg is counterfactual (not deployed). "
            "Use USDC-oracle prepared files for on-chain hook / auditor headlines."
        )

    if args.timeline_only:
        return

    models = {
        "Static (current)": static_fee,
        "Oscillon additive (base + tax)": oscillon_fee_additive,
        "Oscillon hybrid": oscillon_fee_hybrid,
        "Oscillon piecewise (3-zone)": oscillon_fee_piecewise,
        "Oscillon K=45 (quadratic)": oscillon_fee_k45,
        "Oscillon no threshold": oscillon_fee_no_threshold,
    }
    reference_models = {
        "Ref: dev×1.05 ceiling": paper_minimum_fee,
    }

    print("\n" + "=" * 70)
    print("METHODOLOGY")
    print("=" * 70)
    print(
        "• LP income + LVR = depeg spread on each drain swap (when fee ≤ dev).\n"
        "  Fee models SPLIT the same spread — totals match across Oscillon variants.\n"
        "  Compare LP Capture %, not LP+LVR sum.\n"
        f"• Volume Lost: {'APPLIED to drain volume (routing model)' if args.apply_routing else 'REPORTED ONLY — does not change LP/LVR unless --apply-routing'}\n"
        "• Ref dev×1.05: academic ceiling (fee > dev → LVR=0). Not deployable.\n"
        "• Rows with swap_size_usd < $100 dropped at prepare time (see prepare_data --min-swap-usd)."
    )

    all_results: dict[str, pd.DataFrame] = {}
    for model_name, fee_fn in {**models, **reference_models}.items():
        records: list[dict] = []
        for _, swap in swaps.iterrows():
            records.append(
                simulate_swap_row(
                    swap,
                    fee_fn,
                    curve_fee_bps=args.curve_fee_bps,
                    apply_routing=args.apply_routing,
                    volume_eta=args.volume_eta,
                )
            )

        all_results[model_name] = pd.DataFrame(records)
        tag = " [reference]" if model_name in reference_models else ""
        print(f"Processed {model_name}{tag}: {len(records):,} swaps")

    print("\n" + "=" * 70)
    print("BACKTEST RESULTS SUMMARY")
    print("=" * 70)

    summary_rows = []
    production_names = list(models.keys())
    for model_name, df in all_results.items():
        s = summarize_backtest(df, tvl=args.tvl, period_days=period_days)
        is_ref = model_name in reference_models
        summary_rows.append(
            {
                "Model": model_name + (" *" if is_ref else ""),
                "LP Income ($)": f"${s['lp_income_usd']:,.0f}",
                "LVR ($)": f"${s['lvr_usd']:,.0f}",
                "Spread (LP+LVR)": f"${s['spread_captured_usd']:,.0f}",
                "LP Capture %": f"{s['lp_capture_pct']:.1f}%",
                "Volume Lost %": f"{s['volume_lost_pct']:.1f}%",
                "Stress APR": f"{s['stress_apr']:.2f}%",
            }
        )

    summary = pd.DataFrame(summary_rows)
    print(summary.to_string(index=False))
    print("\n* Reference model only — fee often exceeds depeg severity (not deployable).")
    print("  Production comparison: use LP Capture % and LP Income; Spread column should")
    print("  match across Oscillon variants when fees stay below dev_bps.")

    # Large-depeg capture honesty check
    base_df = all_results["Static (current)"]
    large_mask = base_df["dev_bps"] >= 30
    if large_mask.any():
        static_large = base_df.loc[large_mask]
        hyb_df = all_results["Oscillon hybrid"].loc[large_mask]
        static_cap = (
            static_large["lp_income"].sum()
            / (static_large["lp_income"].sum() + static_large["lvr"].sum())
            * 100
        )
        hyb_cap = (
            hyb_df["lp_income"].sum()
            / (hyb_df["lp_income"].sum() + hyb_df["lvr"].sum())
            * 100
        )
        print(
            f"\nLarge depeg (30+ bps): Oscillon hybrid captures {hyb_cap:.1f}% of spread "
            f"vs static {static_cap:.1f}% — not 100%; most LVR remains on table."
        )

    print("\n" + "=" * 70)
    print("RESULTS BY MARKET CONDITION")
    print("=" * 70)

    conditions = {
        "Normal (0-3 bps)": (0, 3),
        "Micro-depeg (3-7 bps)": (3, 7),
        "Small depeg (7-15 bps)": (7, 15),
        "Medium depeg (15-30 bps)": (15, 30),
        "Large depeg (30+ bps)": (30, 999),
    }

    total_swaps = len(base_df)
    for cond_name, (low, high) in conditions.items():
        n_in_cond = ((base_df["dev_bps"] >= low) & (base_df["dev_bps"] < high)).sum()
        pct = (n_in_cond / total_swaps * 100) if total_swaps > 0 else 0.0
        if n_in_cond == 0:
            print(f"\n  {cond_name}: 0 swaps (no observations in this depeg band)")
            continue
        print(f"\n  {cond_name}")
        print(f"  {'Model':<30} {'LP Income':>12} {'LVR':>12} {'Capture%':>10}")
        print(f"  {'-' * 64}")

        for model_name, df in all_results.items():
            mask = (df["dev_bps"] >= low) & (df["dev_bps"] < high)
            subset = df[mask]
            if len(subset) == 0:
                continue
            lp_inc = subset["lp_income"].sum()
            lvr = subset["lvr"].sum()
            total = lp_inc + lvr
            cap = (lp_inc / total * 100) if total > 0 else 0.0
            print(f"  {model_name:<30} ${lp_inc:>10,.0f} ${lvr:>10,.0f} {cap:>9.1f}%")

        print(f"  ({n_in_cond:,} swaps = {pct:.1f}% of all swaps)")

    build_charts(all_results, chart_out=args.chart_out, show_chart=args.show_chart)


if __name__ == "__main__":
    main()
