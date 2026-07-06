#!/usr/bin/env python3
"""
Cross-asset Oscillon scorecard — compare LP capture and surcharge across research cases.

Runs backtests on prepared CSVs that exist; skips missing data with fetch hints.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.asset_research import RESEARCH_CASES, case_by_id, prepared_path
from src.backtest_engine import hybrid_fee_fn, run_prepared_backtest, static_fee_fn, summarize_backtest
from src.oscillon_fee import BASE_FEE_BPS

DEFAULT_TVL = 500_000_000.0


def _period_days(swaps: pd.DataFrame) -> float:
    if "timestamp" not in swaps.columns or swaps.empty:
        return 1.0
    ts = pd.to_datetime(swaps["timestamp"])
    days = (ts.max() - ts.min()).total_seconds() / 86400.0
    return max(days, 1.0)


def score_one_case(case, *, tvl: float, root: Path) -> dict:
    path = prepared_path(case, root=root)
    if not path.exists():
        return {
            "asset_id": case.asset_id,
            "label": case.label,
            "status": "missing_data",
            "prepared_csv": str(path),
            "fetch_hint": case.fetch_hint,
            "category": case.category,
            "partnership_tier": case.partnership_tier,
        }

    swaps = pd.read_csv(path)
    period_days = _period_days(swaps)
    static_df = run_prepared_backtest(swaps, static_fee_fn)
    hybrid_df = run_prepared_backtest(swaps, hybrid_fee_fn)
    static_s = summarize_backtest(static_df, tvl=tvl, period_days=period_days)
    hybrid_s = summarize_backtest(hybrid_df, tvl=tvl, period_days=period_days)

    surcharge_usd = hybrid_s["lp_income_usd"] - static_s["lp_income_usd"]
    surcharge_bps_yr = (
        (surcharge_usd / tvl) * (365.0 / period_days) * 10_000 if tvl > 0 else 0.0
    )
    drain_vol = float(swaps.loc[swaps["is_drain"], "drain_volume_usd"].sum())
    avg_dev = float(swaps["dev_bps"].mean())
    max_dev = float(swaps["dev_bps"].max())

    return {
        "asset_id": case.asset_id,
        "label": case.label,
        "status": "ok",
        "category": case.category,
        "partnership_tier": case.partnership_tier,
        "period_label": case.period_label,
        "period_days": round(period_days, 2),
        "oracle_source": case.oracle_source,
        "reference_mode": case.reference_mode,
        "swaps": len(swaps),
        "drain_swaps": int(swaps["is_drain"].sum()),
        "drain_volume_usd": drain_vol,
        "avg_dev_bps": round(avg_dev, 2),
        "max_dev_bps": round(max_dev, 2),
        "static_capture_pct": round(static_s["lp_capture_pct"], 1),
        "hybrid_capture_pct": round(hybrid_s["lp_capture_pct"], 1),
        "capture_uplift_pp": round(
            hybrid_s["lp_capture_pct"] - static_s["lp_capture_pct"], 1
        ),
        "static_lp_usd": round(static_s["lp_income_usd"], 2),
        "hybrid_lp_usd": round(hybrid_s["lp_income_usd"], 2),
        "surcharge_usd": round(surcharge_usd, 2),
        "surcharge_bps_per_year": round(surcharge_bps_yr, 2),
        "tvl_assumed": tvl,
        "prepared_csv": str(path),
    }


def render_markdown(rows: list[dict], *, tvl: float) -> str:
    lines = [
        "# Oscillon cross-asset scorecard",
        "",
        f"Assumed TVL: **${tvl:,.0f}** | Base fee: **{BASE_FEE_BPS} bps** | Model: **hybrid**",
        "",
        "## Summary table",
        "",
        "| Asset | Period | Avg dev | Max dev | Static cap% | Hybrid cap% | Δ cap | Surcharge $ | Surcharge bps/yr | Status |",
        "|-------|--------|---------|---------|-------------|-------------|-------|-------------|------------------|--------|",
    ]
    for r in rows:
        if r["status"] != "ok":
            lines.append(
                f"| {r['label']} | — | — | — | — | — | — | — | — | **missing** |"
            )
            continue
        lines.append(
            f"| {r['label']} | {r['period_label']} | {r['avg_dev_bps']} | "
            f"{r['max_dev_bps']} | {r['static_capture_pct']}% | {r['hybrid_capture_pct']}% | "
            f"+{r['capture_uplift_pp']}pp | ${r['surcharge_usd']:,.0f} | "
            f"**{r['surcharge_bps_per_year']:.2f}** | ok |"
        )

    lines.extend(["", "## Missing data — fetch hints", ""])
    for r in rows:
        if r["status"] == "missing_data" and r.get("fetch_hint"):
            lines.append(f"- **{r['label']}**: `{r['fetch_hint']}`")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- **Capture %** = LP fees / (LP fees + LVR) on drain swaps — crisis metric.",
            "- **Surcharge bps/yr** = incremental hybrid LP $ above static 3 bps, annualized ÷ TVL.",
            "- High capture + low bps/yr = micro-depeg calm regime (USDC H1 2026).",
            "- High capture + high bps/yr = persistent peg friction (target customer).",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Cross-asset Oscillon research scorecard")
    p.add_argument("--tvl", type=float, default=DEFAULT_TVL)
    p.add_argument("--asset", action="append", default=[], help="Filter to asset_id (repeatable)")
    p.add_argument("--out-md", default="output/asset_scorecard.md")
    p.add_argument("--out-json", default="output/asset_scorecard.json")
    args = p.parse_args()

    cases = RESEARCH_CASES
    if args.asset:
        cases = tuple(case_by_id(a) for a in args.asset)

    rows = [score_one_case(c, tvl=args.tvl, root=ROOT) for c in cases]
    md = render_markdown(rows, tvl=args.tvl)

    out_md = ROOT / args.out_md
    out_json = ROOT / args.out_json
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)
    out_json.write_text(json.dumps(rows, indent=2))
    print(md)
    print(f"\nWrote {out_md} and {out_json}")


if __name__ == "__main__":
    main()
