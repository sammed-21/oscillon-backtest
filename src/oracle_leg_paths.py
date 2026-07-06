"""Paths and labels for USDC vs USDT oracle-leg backtests (never mixed)."""

from __future__ import annotations

from pathlib import Path

from .swap_direction import OracleLeg

# token0 = USDC/USD + netAmount0 (deployed hook)
# token1 = USDT/USD + netAmount1 (counterfactual only)
LEG_TAGS: dict[OracleLeg, str] = {
    "token0": "usdc_oracle",
    "token1": "usdt_oracle",
}

LEG_LABELS: dict[OracleLeg, str] = {
    "token0": "USDC oracle (token0 leg — on-chain hook)",
    "token1": "USDT oracle (token1 leg — counterfactual, not deployed)",
}

DEFAULT_ORACLE_FILES: dict[OracleLeg, str] = {
    "token0": "data/chainlink_usdc_2026_h1.csv",
    "token1": "data/chainlink_usdt_2026_h1.csv",
}


def period_slug(start: str, end: str) -> str:
    """e.g. 2026-01-01 + 2026-06-30 → 2026_h1 if Jan–Jun same year else 2026-01-01_2026-06-30."""
    ys, ye = start[:4], end[:4]
    if ys == ye and start.endswith("-01-01") and end.endswith("-06-30"):
        return f"{ys}_h1"
    if start == end:
        return start
    return f"{start}_{end}"


def prepared_csv(start: str, end: str, leg: OracleLeg, *, data_dir: str = "data") -> Path:
    tag = LEG_TAGS[leg]
    slug = period_slug(start, end)
    return Path(data_dir) / f"prepared_swaps_{slug}_{tag}.csv"


def backtest_outputs(start: str, end: str, leg: OracleLeg, *, out_dir: str = "output") -> dict[str, Path]:
    tag = LEG_TAGS[leg]
    slug = period_slug(start, end)
    base = Path(out_dir)
    stem = f"backtest_{slug}_{tag}"
    return {
        "chart": base / f"{stem}.png",
        "timeline_csv": base / f"depeg_fee_timeline_{slug}_{tag}.csv",
        "timeline_png": base / f"depeg_fee_timeline_{slug}_{tag}.png",
        "summary_md": base / f"{stem}_summary.md",
    }


def oracle_asset_for_leg(leg: OracleLeg) -> str:
    return "USDC" if leg == "token0" else "USDT"
