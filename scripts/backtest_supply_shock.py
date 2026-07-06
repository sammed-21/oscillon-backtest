#!/usr/bin/env python3
"""CLI for supply-shock (mint-and-dump) scenario backtests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.supply_shock_analysis import (
    build_scenario_comparison_markdown,
    run_stabl_r_default_comparison,
    run_supply_shock_comparison,
    summarize_organic_arbitrage_from_prepared,
    supply_shock_summary_dict,
)
from src.supply_shock_scenario import (
    SupplyShockScenario,
    calibrate_pool_depth_for_target_proceeds,
    stabl_r_default,
)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Supply-shock mint-and-dump scenario (StablR-class attacks)"
    )
    p.add_argument(
        "--preset",
        choices=["stabl-r"],
        default="stabl-r",
        help="Default parameter set (StablR May 2026)",
    )
    p.add_argument("--mint-face-value", type=float, help="USD minted and dumped")
    p.add_argument("--dump-blocks", type=int, default=1, help="Spread dump over N blocks")
    p.add_argument("--pool-liquidity-depth", type=float, help="USD reserve per side at peg")
    p.add_argument("--initial-dev-bps", type=float, default=6.0)
    p.add_argument(
        "--calibrate-to-proceeds",
        type=float,
        metavar="USD",
        help="Set pool depth so static-fee gross proceeds match this target",
    )
    p.add_argument(
        "--organic-prepared",
        default="data/prepared_swaps.csv",
        help="Prepared swaps CSV for organic-arbitrage comparison table",
    )
    p.add_argument(
        "--out-json",
        default="output/supply_shock_summary.json",
    )
    p.add_argument(
        "--out-md",
        default="output/scenario_comparison.md",
    )
    args = p.parse_args()

    if args.preset == "stabl-r" and args.mint_face_value is None:
        scenario = stabl_r_default()
        if args.dump_blocks != 1:
            scenario = SupplyShockScenario(
                mint_face_value=scenario.mint_face_value,
                dump_blocks=args.dump_blocks,
                pool_liquidity_depth=scenario.pool_liquidity_depth,
                initial_dev_bps=args.initial_dev_bps,
            )
    else:
        mint = args.mint_face_value or 10_400_000.0
        depth = args.pool_liquidity_depth
        if args.calibrate_to_proceeds:
            depth = calibrate_pool_depth_for_target_proceeds(
                mint, args.calibrate_to_proceeds, initial_dev_bps=args.initial_dev_bps
            )
        elif depth is None:
            depth = 3_830_000.0
        scenario = SupplyShockScenario(
            mint_face_value=mint,
            dump_blocks=args.dump_blocks,
            pool_liquidity_depth=depth,
            initial_dev_bps=args.initial_dev_bps,
        )

    comparison = run_supply_shock_comparison(scenario)
    s, d = comparison.static, comparison.dynamic

    print("=" * 70)
    print("SUPPLY SHOCK SCENARIO — StablR-class mint-and-dump")
    print("=" * 70)
    print(f"Mint face value:     ${scenario.mint_face_value:,.0f}")
    print(f"Pool depth/side:     ${scenario.pool_liquidity_depth:,.0f}")
    print(f"Dump blocks:         {scenario.dump_blocks}")
    print(f"Initial oracle depeg:{scenario.initial_dev_bps:.1f} bps")
    print()
    print(f"{'':30} {'Static fee':>14} {'Oscillon':>14}")
    print("-" * 60)
    print(f"{'Gross proceeds':30} ${s.gross_proceeds_usd:>12,.0f} ${d.gross_proceeds_usd:>12,.0f}")
    print(f"{'Fees paid':30} ${s.total_fees_paid_usd:>12,.0f} ${d.total_fees_paid_usd:>12,.0f}")
    print(f"{'Net attacker payoff':30} ${s.net_attacker_payoff_usd:>12,.0f} ${d.net_attacker_payoff_usd:>12,.0f}")
    print(f"{'LP fee income':30} ${s.lp_fee_income_usd:>12,.0f} ${d.lp_fee_income_usd:>12,.0f}")
    print(f"{'LP net loss':30} ${s.lp_net_loss_usd:>12,.0f} ${d.lp_net_loss_usd:>12,.0f}")
    print()
    print(f"Attacker payoff change from fee escalation: {comparison.attacker_payoff_delta_pct:+.3f}%")
    print(f"LP net loss change from fee escalation:     {comparison.lp_loss_delta_pct:+.3f}%")

    organic = None
    organic_path = Path(args.organic_prepared)
    if organic_path.exists():
        organic = summarize_organic_arbitrage_from_prepared(organic_path)
        print()
        print("=" * 70)
        print("ORGANIC ARBITRAGE REFERENCE (same infra, historical replay)")
        print("=" * 70)
        print(f"Static LP income:   ${organic.static_lp_income_usd:,.0f}")
        print(f"Oscillon LP income: ${organic.dynamic_lp_income_usd:,.0f}  (+{organic.lp_income_improvement_pct:.1f}%)")
        print(f"Static LVR:         ${organic.static_lvr_usd:,.0f}")
        print(f"Oscillon LVR:       ${organic.dynamic_lvr_usd:,.0f}  (−{organic.lvr_reduction_pct:.1f}%)")
        markdown = build_scenario_comparison_markdown(organic, comparison)
    else:
        print()
        print(f"Note: organic comparison skipped ({organic_path} not found)")
        from src.supply_shock_analysis import _supply_only_markdown

        markdown = _supply_only_markdown(comparison)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "scenario": {
            "mint_face_value": scenario.mint_face_value,
            "dump_blocks": scenario.dump_blocks,
            "pool_liquidity_depth": scenario.pool_liquidity_depth,
            "initial_dev_bps": scenario.initial_dev_bps,
        },
        **supply_shock_summary_dict(comparison),
    }
    if organic:
        payload["organic_arbitrage"] = {
            "static_lp_income_usd": organic.static_lp_income_usd,
            "dynamic_lp_income_usd": organic.dynamic_lp_income_usd,
            "static_lvr_usd": organic.static_lvr_usd,
            "dynamic_lvr_usd": organic.dynamic_lvr_usd,
            "lp_income_improvement_pct": organic.lp_income_improvement_pct,
            "lvr_reduction_pct": organic.lvr_reduction_pct,
        }

    out_json.write_text(json.dumps(payload, indent=2))
    out_md.write_text(markdown)
    print()
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")


if __name__ == "__main__":
    main()
