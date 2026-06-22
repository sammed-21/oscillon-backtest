#!/usr/bin/env python3
"""
Sweep K and plot optimization score.

score = lp_revenue - (λ1 × lvr) - (λ2 × volume_loss)

Uses oscillon_fee.py via k_override (same formula as the hook).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from src.k_optimizer import sweep_k_values


def main() -> None:
    p = argparse.ArgumentParser(description="Sweep Oscillon K and plot score")
    p.add_argument("--prepared", default="data/prepared_swaps.csv")
    p.add_argument("--ks", default="20,30,45,60,80", help="Comma-separated K values")
    p.add_argument("--lambda-lvr", type=float, default=1.0, help="λ1: weight on LVR")
    p.add_argument("--lambda-volume", type=float, default=0.5, help="λ2: weight on volume loss")
    p.add_argument("--competitor-fee-bps", type=float, default=4.0)
    p.add_argument("--out-csv", default="output/k_sweep.csv")
    p.add_argument("--out-chart", default="output/k_sweep_score.png")
    p.add_argument("--show", action="store_true")
    args = p.parse_args()

    df = pd.read_csv(args.prepared)
    k_values = [int(x.strip()) for x in args.ks.split(",")]

    results = sweep_k_values(
        df,
        k_values,
        lambda_lvr=args.lambda_lvr,
        lambda_volume=args.lambda_volume,
        competitor_fee_bps=args.competitor_fee_bps,
    )

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.out_csv, index=False)

    best = results.iloc[0]
    print("\nK sweep results (higher score = better tradeoff)")
    print(results.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"\nBest K by score: {int(best['k'])} (score={best['score']:,.2f})")
    print(f"  lp_revenue=${best['lp_revenue_usd']:,.0f}  lvr=${best['lvr_usd']:,.0f}  volume_loss=${best['volume_loss_usd']:,.0f}")
    print(f"\nWrote {args.out_csv}")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    ks = results["k"]
    axes[0].plot(ks, results["score"], "o-", color="#4F5FD4", linewidth=2, markersize=8)
    axes[0].axvline(best["k"], color="orange", linestyle=":", label=f"best K={int(best['k'])}")
    axes[0].set_xlabel("K")
    axes[0].set_ylabel("Score ($)")
    axes[0].set_title(
        f"score = revenue - {args.lambda_lvr}×LVR - {args.lambda_volume}×volume_loss"
    )
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()

    x = range(len(results))
    w = 0.25
    axes[1].bar([i - w for i in x], results["lp_revenue_usd"], width=w, label="LP revenue", color="#00A388")
    axes[1].bar(x, results["lvr_usd"], width=w, label="LVR", color="#FF6B6B")
    axes[1].bar([i + w for i in x], results["volume_loss_usd"], width=w, label="Volume loss", color="#6A7A8A")
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels([str(int(k)) for k in ks])
    axes[1].set_xlabel("K")
    axes[1].set_ylabel("USD")
    axes[1].set_title("Components by K")
    axes[1].legend(fontsize=8)
    axes[1].grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(args.out_chart, dpi=150, bbox_inches="tight")
    print(f"Wrote {args.out_chart}")
    if args.show:
        plt.show()
    plt.close(fig)


if __name__ == "__main__":
    main()
