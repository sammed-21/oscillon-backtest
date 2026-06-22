#!/usr/bin/env python3
"""APR / APY from output/summary.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.apr import compute_apr

DEFAULT_SUMMARY = ROOT / "output/summary.json"


def main() -> None:
    p = argparse.ArgumentParser(
        description="LP fee yield APR/APY (earnings) and net return after LVR"
    )
    p.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    p.add_argument("--days",    type=float, default=1.0)
    p.add_argument("--tvl",     type=float, default=2_500_000)
    p.add_argument("--capital", type=float, default=100_000)
    p.add_argument("--out",     default=str(ROOT / "output/apr.json"))
    args = p.parse_args()

    # ── resolve args first, before any use ──────────────────────────────────
    lp_capital_usd = args.capital   # FIX: was used before assignment in original
    pool_tvl_usd   = args.tvl
    days           = args.days

    path = Path(args.summary)
    if not path.exists():
        print(f"Missing {path}. Run: python3 scripts/backtest.py --start DATE --end DATE")
        sys.exit(1)

    s = json.loads(path.read_text())

    # ── validate required keys exist in summary.json ─────────────────────────
    required_keys = [
        "fees_collected_static_usd",
        "fees_collected_dynamic_usd",
        "lp_net_loss_static_usd",
        "lp_net_loss_dynamic_usd",
    ]
    missing = [k for k in required_keys if k not in s]
    if missing:
        print(f"ERROR: summary.json is missing keys: {missing}")
        sys.exit(1)

    pool_fees_s = float(s["fees_collected_static_usd"])
    pool_fees_d = float(s["fees_collected_dynamic_usd"])
    extra       = float(s.get("extra_fees_dynamic_usd", pool_fees_d - pool_fees_s))

    r = compute_apr(
        fees_static_usd        = pool_fees_s,
        fees_dynamic_usd       = pool_fees_d,
        lp_net_lvr_usd_static  = float(s["lp_net_loss_static_usd"]),
        lp_net_lvr_usd_dynamic = float(s["lp_net_loss_dynamic_usd"]),
        lp_capital_usd         = lp_capital_usd,
        pool_tvl_usd           = pool_tvl_usd,
        days                   = days,
    )

    # ── safe lp_share: compute locally in case dataclass doesn't expose it ───
    lp_share = lp_capital_usd / pool_tvl_usd if pool_tvl_usd > 0 else 0.0

    # ── print report ─────────────────────────────────────────────────────────
    print(f"Period: {days:g} day(s) | Your LP ${lp_capital_usd:,.0f} | Pool TVL ${pool_tvl_usd:,.0f}")
    print(f"  Your share of pool: {lp_share * 100:.2f}%")
    print()

    print("  Pool total fee revenue (whole pool, not annualized):")
    print(f"    Static 1 bps : ${pool_fees_s:,.2f}")
    print(f"    Oscillon     : ${pool_fees_d:,.2f}  ({extra:+,.2f} vs static)")
    if pool_fees_d < pool_fees_s:
        print(
            "    Note: Oscillon often charges a *higher fee per swap* during depeg,\n"
            "    but this model assumes routers send some volume to a 1 bps competitor\n"
            "    when Oscillon's fee is higher → slightly fewer fee *dollars*.\n"
            "    Oscillon's benefit here is lower LVR (less toxic flow), not max fees."
        )
    print()

    print("  Dollars over this period (your LP share):")
    print(f"    Fee income : static ${r.lp_fees_static_usd:,.2f}   | Oscillon ${r.lp_fees_dynamic_usd:,.2f}")
    print(f"    LVR drag   : static ${r.lp_lvr_drag_static_usd:,.2f}   | Oscillon ${r.lp_lvr_drag_dynamic_usd:,.2f}")
    print(f"    Net PnL    : static ${r.lp_net_pnl_static_usd:,.2f}   | Oscillon ${r.lp_net_pnl_dynamic_usd:,.2f}")
    print()

    print("  Fee yield (annualized):")
    print(f"    Fee APR : static {r.fee_apr_static_pct:+.2f}%  | Oscillon {r.fee_apr_dynamic_pct:+.2f}%")
    print(f"    Fee APY : static {r.fee_apy_static_pct:+.2f}%  | Oscillon {r.fee_apy_dynamic_pct:+.2f}%")
    print(f"    Pool fee APR (all LPs): {r.pool_fee_apr_static_pct:.2f}% static | {r.pool_fee_apr_dynamic_pct:.2f}% Oscillon")
    print()

    print("  LVR drag (annualized adverse-selection cost):")
    print(f"    LVR APR : static {r.lvr_apr_static_pct:.2f}%  | Oscillon {r.lvr_apr_dynamic_pct:.2f}%")
    print()

    print("  Net return (fee yield − LVR drag):")
    print(f"    Net APR : static {r.net_apr_static_pct:+.2f}%  | Oscillon {r.net_apr_dynamic_pct:+.2f}%")
    print(f"    Net APY : static {r.net_apy_static_pct:+.2f}%  | Oscillon {r.net_apy_dynamic_pct:+.2f}%")

    improvement_bps = getattr(r, "net_apr_improvement_bps", None)
    if improvement_bps is not None and improvement_bps > 0:
        print(f"    Oscillon net APR better by {improvement_bps:.0f} bps vs static")

    # ── write output ─────────────────────────────────────────────────────────
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "summary": s,
                "apr": {k: getattr(r, k) for k in r.__dataclass_fields__},
                "meta": {
                    "days":           days,
                    "lp_capital_usd": lp_capital_usd,
                    "pool_tvl_usd":   pool_tvl_usd,
                    "lp_share":       lp_share,
                },
            },
            indent=2,
        )
    )
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()