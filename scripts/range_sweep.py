#!/usr/bin/env python3
"""
Range-width sweep across all prepared assets: for each candidate range width,
report % of time the pool's real tick price stayed in range and how many
distinct excursion episodes occurred (not just row counts, which double-count
a single sustained excursion across many minutes).

Uses pool_price deviation from $1.00 — the actual tick-level price a
concentrated-liquidity position's range check would use, not the oracle feed
(oracle update cadence understates real range risk, per prior findings).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

TICK_BASE = 1.0001  # Uniswap: price = TICK_BASE ** tick


def bps_to_ticks(width_bps: float, center_price: float = 1.0) -> tuple[int, int]:
    """Symmetric ±width_bps range around center_price -> (tickLower, tickUpper)."""
    upper_price = center_price * (1 + width_bps / 10_000)
    lower_price = center_price * (1 - width_bps / 10_000)
    tick_upper = round(math.log(upper_price) / math.log(TICK_BASE))
    tick_lower = round(math.log(lower_price) / math.log(TICK_BASE))
    return tick_lower, tick_upper

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Validated categorical palette (dataviz skill reference palette, light mode,
# slots 1-5 in fixed order) — passes CVD + normal-vision separation for lines.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]

DATASETS = [
    ("USDC/USDT — June 2026 (calm)", "data/prepared_swaps_2026-06.csv"),
    ("USDC/USDT — Mar 2023 (SVB stress)", "data/prepared_swaps_2023-03.csv"),
    ("USDC/USDT — 12mo blended (Jul25-Jun26)", "data/prepared_swaps_2025-07_2026-06_usdc_oracle.csv"),
    ("USDe/USDT — 10.5mo real (Oct25-Aug26)", "data/prepared_swaps_usde_usdt.csv"),
    ("USDe/USDC — 5wk real (Jul-Aug26)", "data/prepared_swaps_usde_usdc.csv"),
]

WIDTHS_BPS = [3, 5, 10, 15, 20, 30, 50, 100, 200, 500]


def sweep_one(path: str) -> pd.DataFrame | None:
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["timestamp"])
    dev = (df["pool_price"] - 1.0).abs() * 10_000

    rows = []
    for w in WIDTHS_BPS:
        out = dev > w
        in_range_pct = (1 - out.mean()) * 100.0
        prev_out = out.shift(1, fill_value=False)
        episodes = int((out & ~prev_out).sum())
        tick_lower, tick_upper = bps_to_ticks(w)
        rows.append(
            {
                "width_bps": w,
                "tick_lower": tick_lower,
                "tick_upper": tick_upper,
                "width_ticks": tick_upper - tick_lower,
                "in_range_pct": round(in_range_pct, 2),
                "out_of_range_rows": int(out.sum()),
                "excursion_episodes": episodes,
            }
        )
    return pd.DataFrame(rows), dev, df


def best_width(table: pd.DataFrame, threshold: float = 99.9) -> int | None:
    ok = table[table["in_range_pct"] >= threshold]
    return int(ok.iloc[0]["width_bps"]) if len(ok) else None


def build_chart(all_tables: dict[str, pd.DataFrame], out_path: str) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.2))

    for i, (label, table) in enumerate(all_tables.items()):
        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        x = table["width_bps"]
        ax1.plot(x, table["in_range_pct"], marker="o", markersize=4.5, linewidth=2, color=color, label=label)
        ax2.plot(x, table["excursion_episodes"], marker="o", markersize=4.5, linewidth=2, color=color, label=label)
        best = best_width(table)
        if best is not None:
            row = table[table["width_bps"] == best].iloc[0]
            ax1.scatter([best], [row["in_range_pct"]], s=90, facecolors="none",
                        edgecolors=color, linewidths=2, zorder=5)

    ax1.set_xscale("log")
    ax1.set_xlabel("Range width (± bps)")
    ax1.set_ylabel("Time in range (%)")
    ax1.set_title("How much of each range width keeps liquidity active\n(ringed point = narrowest width reaching ≥ 99.9% in-range)")
    ax1.set_ylim(0, 103)
    ax1.grid(True, alpha=0.25, which="both")
    ax1.axhline(99.9, color="#8a8a86", linestyle=":", linewidth=1, alpha=0.6)
    # 1 bps of price move == ~1 tick at this price level (tick = 1.0001^n), so
    # total range width in ticks == ~2x the bps value; secondary axis makes the
    # tick-count meaning of each width visible without a second table.
    tick_axis = ax1.secondary_xaxis("top", functions=(lambda w: 2 * w, lambda t: t / 2))
    tick_axis.set_xlabel("Total range width (ticks, tickUpper − tickLower)")

    ax2.set_xscale("log")
    ax2.set_yscale("symlog")
    ax2.set_xlabel("Range width (± bps)")
    ax2.set_ylabel("Distinct excursion episodes (out-of-range events)")
    ax2.set_title("How often a rebalance would fire at each width")
    ax2.grid(True, alpha=0.25, which="both")

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False,
               fontsize=9, bbox_to_anchor=(0.5, -0.04))

    fig.suptitle("Range Selection Across Assets & Regimes — Real On-Chain Data", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0.06, 1, 0.94])
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\nChart saved: {out_path}")


def main() -> None:
    all_tables: dict[str, pd.DataFrame] = {}
    print("BEST RANGE (narrowest width ≥ 99.9% in-range) — summary\n")
    for label, path in DATASETS:
        result = sweep_one(path)
        if result is None:
            print(f"## {label}\n  [missing: {path}]\n")
            continue
        table, dev, df = result
        all_tables[label] = table
        period_days = (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400
        best = best_width(table)
        if best is not None:
            bl, bu = bps_to_ticks(best)
            best_str = f"±{best} bps  [tickLower={bl}, tickUpper={bu}]"
        else:
            best_str = "none up to ±500 bps"
        print(f"## {label}")
        print(f"   {len(df):,} swaps over {period_days:.1f} days | "
              f"mean dev {dev.mean():.2f} bps | max dev {dev.max():.2f} bps | best range: {best_str}")
        print(f"   {'Width':>8} | {'Ticks [lo,hi]':>18} | {'In-range %':>10} | {'Out rows':>9} | {'Episodes':>9}")
        print(f"   {'-'*8}-+-{'-'*18}-+-{'-'*10}-+-{'-'*9}-+-{'-'*9}")
        for _, r in table.iterrows():
            tick_str = f"[{int(r.tick_lower)},{int(r.tick_upper)}]"
            print(f"   ±{int(r.width_bps):>6} | {tick_str:>18} | {r.in_range_pct:>9.2f}% | {int(r.out_of_range_rows):>9,} | {int(r.excursion_episodes):>9}")
        print()

    if all_tables:
        build_chart(all_tables, "output/range_sweep_all_assets.png")


if __name__ == "__main__":
    main()
