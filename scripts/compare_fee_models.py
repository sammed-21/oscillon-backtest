#!/usr/bin/env python3
"""Compare piecewise vs quadratic K=45 on prepared_swaps.csv."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from src.k_optimizer import evaluate_k_on_prepared
from src.oscillon_fee import oscillon_fee_bps, select_fee_bps


def fee_ladder() -> pd.DataFrame:
    rows = []
    for d in range(0, 101, 5):
        rows.append(
            {
                "dev_bps": d,
                "piecewise_drain": select_fee_bps(d, True, fee_model="piecewise"),
                "quadratic_k45": oscillon_fee_bps(d, True, k=45, fee_model="quadratic"),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--prepared", default="data/prepared_swaps.csv")
    p.add_argument("--lambda-lvr", type=float, default=1.0)
    p.add_argument("--lambda-volume", type=float, default=0.001)
    p.add_argument("--competitor-fee-bps", type=float, default=1.0)
    p.add_argument("--out", default="output/fee_model_compare.png")
    args = p.parse_args()

    df = pd.read_csv(args.prepared)
    ladder = fee_ladder()
    print("\nFee ladder (drain, bps):")
    print(ladder.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # Piecewise: use k=0 placeholder; evaluate uses oscillon_fee_bps with piecewise default
    # We need evaluate for piecewise - add via custom loop here using fee_model
    from src.oscillon_fee import FeeContext, fee_bps, select_fee_pips
    from src.volume_model import retained_volume_fraction

    def eval_model(name: str, fee_model: str, k: int | None) -> dict:
        lp_rev = lvr = vol_loss = 0.0
        for _, row in df.iterrows():
            dev = int(row["dev_bps"])
            drain = bool(row["is_drain"])
            size = float(row.get("swap_size_usd", 0))
            if size <= 0:
                continue
            ctx = FeeContext(dev, drain, k_override=k, fee_model=fee_model)  # type: ignore
            f = fee_bps(select_fee_pips(ctx))
            retain = retained_volume_fraction(f, args.competitor_fee_bps)
            eff = size * retain
            lp_rev += eff * (f / 10_000)
            if drain and dev > 0:
                lvr += max(0.0, eff * (dev - f) / 10_000)
            vol_loss += size * (1 - retain)
        score = lp_rev - args.lambda_lvr * lvr - args.lambda_volume * vol_loss
        return {
            "model": name,
            "lp_revenue": lp_rev,
            "lvr": lvr,
            "volume_loss": vol_loss,
            "score": score,
        }

    q45 = evaluate_k_on_prepared(
        df, 45,
        lambda_lvr=args.lambda_lvr,
        lambda_volume=args.lambda_volume,
        competitor_fee_bps=args.competitor_fee_bps,
    )
    pw = eval_model("piecewise", "piecewise", None)

    print("\nBacktest comparison (same data, competitor={} bps):".format(args.competitor_fee_bps))
    for r in [pw, {
        "model": "quadratic K=45",
        "lp_revenue": q45.lp_revenue_usd,
        "lvr": q45.lvr_usd,
        "volume_loss": q45.volume_loss_usd,
        "score": q45.score,
    }]:
        print(
            f"  {r['model']:<18} revenue=${r['lp_revenue']:,.0f}  "
            f"LVR=${r['lvr']:,.0f}  vol_loss=${r['volume_loss']:,.0f}  score=${r['score']:,.0f}"
        )

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(ladder["dev_bps"], ladder["piecewise_drain"], label="Piecewise", linewidth=2)
    axes[0].plot(ladder["dev_bps"], ladder["quadratic_k45"], label="Quadratic K=45", linestyle="--")
    axes[0].axhline(4, color="gray", alpha=0.5, label="Curve 4 bps")
    axes[0].set_xlabel("Depeg (bps)")
    axes[0].set_ylabel("Fee (bps)")
    axes[0].set_title("Fee curve (drain)")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    models = ["piecewise", "quadratic K=45"]
    scores = [pw["score"], q45.score]
    axes[1].bar(models, scores, color=["#4F5FD4", "#6A7A8A"])
    axes[1].set_ylabel("Score ($)")
    axes[1].set_title("λ score on prepared_swaps")
    axes[1].grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(args.out, dpi=150)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
