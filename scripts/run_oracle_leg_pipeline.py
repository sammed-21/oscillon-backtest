#!/usr/bin/env python3
"""
Prepare and backtest USDC vs USDT oracle legs separately — no price mixing.

USDC (token0): Chainlink USDC/USD + netAmount0 drain — matches deployed hook.
USDT (token1): Chainlink USDT/USD + netAmount1 drain — counterfactual only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest_engine import run_prepared_backtest, summarize_backtest
from src.oracle_leg_paths import (
    DEFAULT_ORACLE_FILES,
    LEG_LABELS,
    LEG_TAGS,
    backtest_outputs,
    oracle_asset_for_leg,
    prepared_csv,
)
from src.swap_direction import OracleLeg
from scripts.backtest_mainnet import oscillon_fee_hybrid, static_fee

PYTHON = sys.executable


def _run(cmd: list[str]) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def prepare_leg(
    *,
    start: str,
    end: str,
    leg: OracleLeg,
    oracle: Path,
    min_swap_usd: float,
) -> Path:
    out = prepared_csv(start, end, leg)
    _run(
        [
            PYTHON,
            str(ROOT / "scripts/prepare_data.py"),
            "--use-minute-files",
            "--start",
            start,
            "--end",
            end,
            "--oracle",
            str(oracle),
            "--oracle-leg",
            leg,
            "--min-swap-usd",
            str(min_swap_usd),
            "--out",
            str(out),
        ]
    )
    return out


def backtest_leg(*, start: str, end: str, leg: OracleLeg, prepared: Path, tvl: float) -> Path:
    outs = backtest_outputs(start, end, leg)
    outs["chart"].parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            PYTHON,
            str(ROOT / "scripts/backtest_mainnet.py"),
            "--prepared",
            str(prepared),
            "--tvl",
            str(tvl),
            "--chart-out",
            str(outs["chart"]),
            "--timeline-csv",
            str(outs["timeline_csv"]),
            "--timeline-out",
            str(outs["timeline_png"]),
        ]
    )
    return outs["chart"]


def quick_summary(prepared: Path, leg: OracleLeg, tvl: float) -> dict:
    df = pd.read_csv(prepared)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    days = max((df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400.0, 1.0)
    static = run_prepared_backtest(df, static_fee)
    hybrid = run_prepared_backtest(df, oscillon_fee_hybrid)
    s0 = summarize_backtest(static, tvl=tvl, period_days=days)
    s1 = summarize_backtest(hybrid, tvl=tvl, period_days=days)
    return {
        "leg": leg,
        "tag": LEG_TAGS[leg],
        "label": LEG_LABELS[leg],
        "oracle_asset": oracle_asset_for_leg(leg),
        "prepared": str(prepared),
        "rows": len(df),
        "period_days": days,
        "drain_swaps": int(df["is_drain"].sum()),
        "drain_volume_usd": float(df["drain_volume_usd"].sum()),
        "avg_dev_bps": float(df["dev_bps"].mean()),
        "max_dev_bps": float(df["dev_bps"].max()),
        "static_lp": s0["lp_income_usd"],
        "hybrid_lp": s1["lp_income_usd"],
        "hybrid_uplift": s1["lp_income_usd"] - s0["lp_income_usd"],
        "static_lvr": s0["lvr_usd"],
        "hybrid_lvr": s1["lvr_usd"],
        "hybrid_capture_pct": s1["lp_capture_pct"],
        "stress_apr": s1["stress_apr"],
    }


def write_comparison_md(rows: list[dict], *, start: str, end: str, out: Path) -> None:
    lines = [
        "# Oracle leg backtests (USDC vs USDT — separate feeds)",
        "",
        f"Period: **{start} → {end}**",
        "",
        "> **USDC** = deployed Oscillon hook path. **USDT** = counterfactual only; do not publish as product performance.",
        "",
        "## Summary",
        "",
        "| Leg | Oracle | Prepared file | Drain swaps | Avg dev | Hybrid LP | Uplift | Capture % |",
        "|-----|--------|---------------|-------------|---------|-----------|--------|-----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['leg']} | {r['oracle_asset']} | `{Path(r['prepared']).name}` | "
            f"{r['drain_swaps']:,} | {r['avg_dev_bps']:.2f} bps | "
            f"${r['hybrid_lp']:,.0f} | +${r['hybrid_uplift']:,.0f} | {r['hybrid_capture_pct']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## Outputs per leg",
            "",
        ]
    )
    for r in rows:
        outs = backtest_outputs(start, end, r["leg"])
        lines.append(f"### {r['label']}")
        lines.append(f"- Prepared: `{r['prepared']}`")
        lines.append(f"- Chart: `{outs['chart']}`")
        lines.append(f"- Timeline: `{outs['timeline_csv']}`, `{outs['timeline_png']}`")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Separate USDC and USDT oracle-leg pipeline")
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-06-30")
    p.add_argument("--usdc-oracle", default=DEFAULT_ORACLE_FILES["token0"])
    p.add_argument("--usdt-oracle", default=DEFAULT_ORACLE_FILES["token1"])
    p.add_argument("--leg", choices=["both", "usdc", "usdt"], default="both")
    p.add_argument("--skip-prepare", action="store_true")
    p.add_argument("--skip-backtest", action="store_true")
    p.add_argument("--tvl", type=float, default=15_000_000)
    p.add_argument("--min-swap-usd", type=float, default=100.0)
    p.add_argument(
        "--comparison-out",
        default="",
        help="Default: output/oracle_leg_comparison_{period}.md",
    )
    args = p.parse_args()

    legs: list[tuple[OracleLeg, Path]] = []
    if args.leg in ("both", "usdc"):
        legs.append(("token0", Path(args.usdc_oracle)))
    if args.leg in ("both", "usdt"):
        legs.append(("token1", Path(args.usdt_oracle)))

    for leg, oracle_path in legs:
        if not oracle_path.exists():
            asset = oracle_asset_for_leg(leg)
            raise SystemExit(
                f"Missing {asset} oracle: {oracle_path}\n"
                f"Fetch: python3 scripts/fetch_data.py --dune-only --oracle-asset {asset.lower()} "
                f"--start-date {args.start} --end-date {args.end} --oracle-out {oracle_path}"
            )

    prepared_paths: dict[OracleLeg, Path] = {}
    if not args.skip_prepare:
        for leg, oracle_path in legs:
            prepared_paths[leg] = prepare_leg(
                start=args.start,
                end=args.end,
                leg=leg,
                oracle=oracle_path,
                min_swap_usd=args.min_swap_usd,
            )
    else:
        for leg, _ in legs:
            path = prepared_csv(args.start, args.end, leg)
            if not path.exists():
                raise SystemExit(f"Missing prepared file: {path} (run without --skip-prepare)")
            prepared_paths[leg] = path

    if not args.skip_backtest:
        for leg in prepared_paths:
            backtest_leg(
                start=args.start,
                end=args.end,
                leg=leg,
                prepared=prepared_paths[leg],
                tvl=args.tvl,
            )

    summaries = [quick_summary(prepared_paths[leg], leg, args.tvl) for leg in prepared_paths]
    comp_out = Path(args.comparison_out) if args.comparison_out else (
        Path("output") / f"oracle_leg_comparison_{args.start[:4]}_h1.md"
        if args.start.endswith("-01-01") and args.end.endswith("-06-30")
        else Path("output") / f"oracle_leg_comparison_{args.start}_{args.end}.md"
    )
    write_comparison_md(summaries, start=args.start, end=args.end, out=comp_out)
    pd.DataFrame(summaries).to_csv(comp_out.with_suffix(".csv"), index=False)

    print("\n" + "=" * 72)
    print("ORACLE LEG PIPELINE — USDC and USDT kept separate")
    print("=" * 72)
    for r in summaries:
        print(f"\n{r['label']}")
        print(f"  {r['prepared']}")
        print(f"  drain: {r['drain_swaps']:,}  vol ${r['drain_volume_usd']:,.0f}  "
              f"dev {r['avg_dev_bps']:.2f}/{r['max_dev_bps']:.2f} bps")
        print(f"  hybrid LP ${r['hybrid_lp']:,.0f} (+${r['hybrid_uplift']:,.0f})  "
              f"capture {r['hybrid_capture_pct']:.1f}%")
    print(f"\nWrote {comp_out}")


if __name__ == "__main__":
    main()
