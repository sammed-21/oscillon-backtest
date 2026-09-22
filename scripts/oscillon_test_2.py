#!/usr/bin/env python3
"""
oscillon-test-2: does a skew-gated fee improve LP inventory mix once taxed swaps leave?

Replays a real v4 pool tape (swaps + LP liquidity events) to get the actual LP token
inventory at every swap, then compares:

  vanilla   the tape as it happened (competing pool fee = --vanilla-fee)
  A literal the pseudo-code exactly as written (fee capped at vanilla, skew from tape)
  B gate    worsening swaps beyond the band pay > vanilla and leave (binary routing);
            skew is computed on the HOOK pool's own counterfactual inventory
  C elastic same fee rule, but flow is retained with prob (vanilla/fee)^eta (repo's
            volume model) instead of a hard leave/stay switch

Counterfactual inventory: hook pool inventory = real inventory - cumulative flow of the
swaps that left (first-order; ignores LPs re-reacting and price-path changes).
Skew = token0 share of LP inventory - 0.5 (both stables valued at $1).
No oracle is used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.lp_inventory_replay import replay  # noqa: E402
from src.volume_model import retained_volume_fraction  # noqa: E402

OUT = ROOT / "oscillon-test-2"
POOLS = {
    "usde_usdt": {"dir": "data/oscillon_test_2/usde_usdt", "d0": 18, "d1": 6, "label": "USDe/USDT"},
    "usde_usdc": {"dir": "data/oscillon_test_2/usde_usdc", "d0": 18, "d1": 6, "label": "USDe/USDC"},
}


def load_replay(key: str):
    cfg = POOLS[key]
    d = ROOT / cfg["dir"]
    meta = json.loads((d / "meta.json").read_text())
    sw = pd.read_csv(d / "swaps.csv.gz", dtype=str)
    md = pd.read_csv(d / "modify.csv.gz", dtype=str)
    for df in (sw, md):
        df["blk"] = df["blk"].astype(int)
        df["idx"] = df["idx"].astype(int)
    sw["tick"] = sw["tick"].astype(int)
    res = replay(sw, md, int(meta["init"]["sqrtPriceX96"]), cfg["d0"], cfg["d1"])
    return res, meta


def _skew(v0: float, v1: float) -> float:
    t = v0 + v1
    return 0.0 if t <= 1e-9 else v0 / t - 0.5


def simulate(f: pd.DataFrame, *, variant: str, band: float, k: float, vanilla: float,
             eta: float = 2.0, gate: str = "pre", max_fee: float = 100.0) -> dict:
    """variant in {'B','C'}; band is a skew fraction (0.03 = 3%)."""
    v0, v1 = f["v0_pre"].to_numpy(), f["v1_pre"].to_numpy()
    a0, a1 = f["a0"].to_numpy(), f["a1"].to_numpy()
    size = (np.abs(a0) + np.abs(a1)) / 2.0
    n = len(f)
    D0 = D1 = 0.0
    skew_h = np.empty(n); keep = np.empty(n); fee = np.empty(n)
    h0 = np.empty(n); h1 = np.empty(n)
    clips = 0
    for i in range(n):
        x0, x1 = v0[i] - D0, v1[i] - D1
        if x0 < 0 or x1 < 0:
            clips += 1
            x0, x1 = max(x0, 0.0), max(x1, 0.0)
        s = _skew(x0, x1)
        if gate == "post":
            s_after = _skew(max(x0 + a0[i], 0.0), max(x1 + a1[i], 0.0))
            worsens, excess = abs(s_after) > abs(s) + 1e-12, abs(s_after)
        else:
            worsens, excess = (a0[i] * s > 0), abs(s)
        if worsens and excess > band:
            fee_i = min(vanilla + k * (excess - band), max_fee)
        elif not worsens and s != 0.0:
            fee_i = 0.5 * vanilla          # restoring swap: discount
        else:
            fee_i = vanilla                # worsening but inside the band
        if variant == "B":
            f_i = 0.0 if fee_i > vanilla else 1.0
        else:
            f_i = retained_volume_fraction(fee_i, vanilla, eta=eta)
        D0 += (1.0 - f_i) * a0[i]
        D1 += (1.0 - f_i) * a1[i]
        skew_h[i], keep[i], fee[i], h0[i], h1[i] = s, f_i, fee_i, x0, x1
    last0 = max(f["v0_post"].iloc[-1] - D0, 0.0)
    last1 = max(f["v1_post"].iloc[-1] - D1, 0.0)
    return dict(skew=skew_h, keep=keep, fee=fee, size=size, h0=h0, h1=h1, clips=clips,
                final_mix=last0 / (last0 + last1) if last0 + last1 > 0 else float("nan"))


def summarize(f: pd.DataFrame, r: dict, band: float, vanilla: float) -> dict:
    size, keep = r["size"], r["keep"]
    dt = f["ts"].diff().dt.total_seconds().fillna(0).to_numpy()
    ab = np.abs(r["skew"])
    return {
        "mean_abs_skew": float(ab.mean()),
        "median_abs_skew": float(np.median(ab)),
        "p95_abs_skew": float(np.quantile(ab, 0.95)),
        "pct_swaps_beyond_band": float((ab > band).mean()),
        "time_weighted_abs_skew": float((ab * dt).sum() / dt.sum()) if dt.sum() > 0 else float("nan"),
        "mean_excess_inventory_usd": float((np.abs(r["h0"] - r["h1"]) / 2).mean()),
        "final_token0_share": float(r["final_mix"]),
        "volume_retained_count": float(keep.mean()),
        "volume_retained_usd": float((keep * size).sum() / size.sum()),
        "hook_fee_revenue_usd": float((keep * size * r["fee"]).sum() * 1e-4),
        "vanilla_fee_revenue_usd": float((size * vanilla).sum() * 1e-4),
        "clipped_swaps": int(r["clips"]),
    }


def literal_pseudocode(f: pd.DataFrame, band: float, k: float, vanilla: float) -> dict:
    """The sketch as written: skew from the tape, fee = min(vanilla + k*excess, vanilla)."""
    s = (f["v0_pre"] / (f["v0_pre"] + f["v1_pre"]) - 0.5).to_numpy()
    a0 = f["a0"].to_numpy()
    worsens = (a0 * s) > 0
    excess = np.abs(s) - band
    fee = np.where(worsens & (np.abs(s) > band), np.minimum(vanilla + k * excess, vanilla), 0.5 * vanilla)
    return {
        "swaps_where_fee_exceeds_vanilla": int((fee > vanilla).sum()),
        "max_fee_bps": float(fee.max()),
        "swaps_worsening_beyond_band": int((worsens & (np.abs(s) > band)).sum()),
        "share_of_swaps_that_would_be_taxed_if_cap_removed": float((worsens & (np.abs(s) > band)).mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla-fee", type=float, default=1.0)
    ap.add_argument("--k", type=float, default=50.0)
    ap.add_argument("--band-bps", type=float, default=300.0)
    ap.add_argument("--eta", type=float, default=2.0)
    args = ap.parse_args()
    band = args.band_bps / 10_000.0
    OUT.mkdir(exist_ok=True)

    results: dict = {"params": vars(args), "pools": {}}
    sweeps: dict[str, pd.DataFrame] = {}
    frames: dict[str, pd.DataFrame] = {}
    for key, cfg in POOLS.items():
        res, meta = load_replay(key)
        f = res.frame
        frames[key] = f
        size = (f["a0"].abs() + f["a1"].abs()) / 2
        tvl = f["v0_pre"] + f["v1_pre"]
        qa = {
            "swaps": len(f), "positions": res.n_positions,
            "first_swap": str(f["ts"].iloc[0]), "last_swap": str(f["ts"].iloc[-1]),
            "active_liquidity_match_rate": res.liquidity_match_rate,
            "negative_liquidity_events": res.negative_liquidity_events,
            "swaps_vs_ground_truth_diff": meta["swaps_rows"] - meta["swaps_ground_truth"],
            "tvl_usd_by_month_median": {str(k)[:7]: float(v) for k, v in tvl.groupby(f["ts"].dt.to_period("M")).median().items()},
        }
        vanilla_sum = summarize(f, {"skew": (f["v0_pre"] / tvl - 0.5).to_numpy(), "keep": np.ones(len(f)),
                                    "fee": np.full(len(f), args.vanilla_fee), "size": size.to_numpy(),
                                    "h0": f["v0_pre"].to_numpy(), "h1": f["v1_pre"].to_numpy(), "clips": 0,
                                    "final_mix": f["v0_post"].iloc[-1] / (f["v0_post"].iloc[-1] + f["v1_post"].iloc[-1])},
                                band, args.vanilla_fee)
        out = {"qa": qa, "vanilla": vanilla_sum,
               "A_literal": literal_pseudocode(f, band, args.k, args.vanilla_fee)}
        for name, kw in (("B_gate", dict(variant="B", gate="pre")), ("C_elastic", dict(variant="C", gate="pre"))):
            r = simulate(f, band=band, k=args.k, vanilla=args.vanilla_fee, eta=args.eta, **kw)
            out[name] = summarize(f, r, band, args.vanilla_fee)
        # sub-period robustness (state restarted at each split)
        mid = pd.Timestamp("2026-04-01") if key == "usde_usdt" else f["ts"].iloc[len(f) // 2]
        for tag, sub in (("first_half", f[f["ts"] < mid].reset_index(drop=True)), ("second_half", f[f["ts"] >= mid].reset_index(drop=True))):
            sub_v = summarize(sub, {"skew": (sub["v0_pre"] / (sub["v0_pre"] + sub["v1_pre"]) - 0.5).to_numpy(), "keep": np.ones(len(sub)),
                                    "fee": np.full(len(sub), args.vanilla_fee), "size": ((sub["a0"].abs() + sub["a1"].abs()) / 2).to_numpy(),
                                    "h0": sub["v0_pre"].to_numpy(), "h1": sub["v1_pre"].to_numpy(), "clips": 0,
                                    "final_mix": sub["v0_post"].iloc[-1] / (sub["v0_post"].iloc[-1] + sub["v1_post"].iloc[-1])}, band, args.vanilla_fee)
            r = simulate(sub, variant="B", band=band, k=args.k, vanilla=args.vanilla_fee, gate="pre")
            out[f"robustness_{tag}"] = {"n_swaps": len(sub), "vanilla_mean_abs_skew": sub_v["mean_abs_skew"],
                                        "B_mean_abs_skew": summarize(sub, r, band, args.vanilla_fee)["mean_abs_skew"],
                                        "B_volume_retained_usd": summarize(sub, r, band, args.vanilla_fee)["volume_retained_usd"]}
        # sweep
        rows = []
        for b in (0.03, 0.05, 0.10, 0.20, 0.30):
            for variant, gate in (("B", "pre"), ("B", "post"), ("C", "pre"), ("C", "post")):
                r = simulate(f, band=b, k=args.k, vanilla=args.vanilla_fee, eta=args.eta, variant=variant, gate=gate)
                s = summarize(f, r, b, args.vanilla_fee)
                rows.append({"band_pct": b * 100, "variant": variant, "gate": gate, **s})
        sweeps[key] = pd.DataFrame(rows)
        sweeps[key].to_csv(OUT / f"sweep_{key}.csv", index=False)
        results["pools"][key] = out

        print(f"\n===== {cfg['label']}  ({qa['first_swap'][:10]} -> {qa['last_swap'][:10]}, {len(f):,} swaps) =====")
        print(f"[QA] active-liquidity match {qa['active_liquidity_match_rate']:.4%} | swaps vs ground truth {qa['swaps_vs_ground_truth_diff']:+d}")
        v = out["vanilla"]
        print(f"Vanilla final mix (token0 share): {v['final_token0_share']:.3f}")
        for name in ("B_gate", "C_elastic"):
            o = out[name]
            print(f"{name}: Hook final mix (honest): {o['final_token0_share']:.3f} | Volume retained: {o['volume_retained_count']:.1%} by count, {o['volume_retained_usd']:.1%} by USD | mean|skew| {v['mean_abs_skew']:.3f} -> {o['mean_abs_skew']:.3f}")
        print("A_literal:", out["A_literal"])

    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=float))
    _charts(frames, sweeps, band, args)


def _charts(frames, sweeps, band, args) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"B pre": "#2a78d6", "B post": "#eb6834", "C pre": "#1baf7a", "C post": "#eda100"}
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharey=False)
    for ax, (key, cfg) in zip(axes, POOLS.items()):
        df = sweeps[key]
        f = frames[key]
        tvl = f["v0_pre"] + f["v1_pre"]
        base = float((f["v0_pre"] / tvl - 0.5).abs().mean())
        ax.scatter([100], [base], color="#8a8a86", s=70, zorder=5)
        ax.annotate("vanilla", (100, base), textcoords="offset points", xytext=(-38, 8), fontsize=9, color="#555")
        for (variant, gate), g in df.groupby(["variant", "gate"]):
            g = g.sort_values("band_pct")
            ax.plot(g["volume_retained_usd"] * 100, g["mean_abs_skew"], marker="o", ms=5, lw=1.8,
                    color=colors[f"{variant} {gate}"], label=f"{variant} gate-{gate}")
            for _, row in g.iterrows():
                ax.annotate(f"{row['band_pct']:g}%", (row["volume_retained_usd"] * 100, row["mean_abs_skew"]),
                            textcoords="offset points", xytext=(4, 4), fontsize=7, color=colors[f"{variant} {gate}"])
        ax.set_xlabel("Volume retained by the hook pool (% of USD volume)")
        ax.set_ylabel("Mean |LP skew| (token0 share − 50%)")
        ax.set_title(f"{cfg['label']}: mix improvement vs. volume kept")
        ax.grid(True, alpha=0.25)
    axes[0].legend(fontsize=8, title="labels = dead band", title_fontsize=8)
    fig.suptitle("Skew-gated fee on real LP inventory: every point on the curve pays for mix with volume", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(OUT / "chart_mix_vs_volume.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    for ax, (key, cfg) in zip(axes, POOLS.items()):
        f = frames[key]
        tvl = f["v0_pre"] + f["v1_pre"]
        van = (f["v0_pre"] / tvl - 0.5).abs()
        rb = simulate(f, band=band, k=args.k, vanilla=args.vanilla_fee, variant="B", gate="pre")
        s = pd.Series(np.abs(rb["skew"]), index=f["ts"])
        w = 500
        ax.plot(f["ts"], van.rolling(w, min_periods=50).median(), color="#8a8a86", lw=1.6, label="vanilla (as it happened)")
        ax.plot(f["ts"], s.rolling(w, min_periods=50).median().to_numpy(), color="#2a78d6", lw=1.6, label=f"hook, B gate, band {band:.0%}")
        ax.axhline(band, color="#c9540c", ls=":", lw=1.2, label="dead band")
        ax.set_title(f"{cfg['label']}: rolling-median |skew| ({w}-swap window)")
        ax.set_ylabel("|token0 share − 50%|")
        ax.grid(True, alpha=0.25)
        ax.tick_params(axis="x", rotation=25)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "chart_skew_paths.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
