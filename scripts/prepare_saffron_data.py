#!/usr/bin/env python3
"""
Prepare swap + oracle data for a Saffron Finance vault backtest.

How this differs from the Oscillon stablecoin prep pipeline (scripts/prepare_data.py):
  - Takes an arbitrary --pool-address instead of a hardcoded USDC/USDT preset.
  - Takes --oracle-asset (e.g. NVDA, ETH) instead of assuming a $1.00 peg.
  - Filters output to a single vault's [--vault-start, --vault-end] window.
  - Emits the exact column set src/saffron_lvr.py expects:
      timestamp, drain_volume_usd, pool_price, oracle_price, is_drain, fee_income_usd

This repo's existing prepare_data.py / fetch_data.py pull raw swaps and
Chainlink prices from Dune + BigQuery (demeter-fetch) using pool presets that
are specific to the Oscillon stablecoin pools. A brand-new pool/oracle pair
(e.g. a USDG/NVDA vault) is not part of that preset config, so the actual
data-source calls below are stubbed with a clear TODO rather than silently
guessing at credentials or endpoints this repo does not have configured for
non-stablecoin assets. Wire in the real fetch (Dune query, subgraph, or an
RPC log scan for the pool address) before running this against a live vault.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fetch_pool_swaps(pool_address: str, vault_start: str, vault_end: str) -> pd.DataFrame:
    """Fetch raw swap events for pool_address in [vault_start, vault_end].

    TODO: replace with a real source (Dune query / subgraph / RPC log scan).
    Must return columns: timestamp, drain_volume_usd, pool_price, is_drain,
    fee_income_usd.
    """
    raise NotImplementedError(
        f"No swap data source wired up for pool {pool_address}. "
        "Populate this function with a Dune/subgraph/RPC fetch before running."
    )


def fetch_oracle_prices(oracle_asset: str, vault_start: str, vault_end: str) -> pd.DataFrame:
    """Fetch external oracle prices for oracle_asset in [vault_start, vault_end].

    TODO: replace with a real price feed source (Chainlink, Pyth, or a
    market-data API) for the given asset symbol.
    Must return columns: timestamp, price.
    """
    raise NotImplementedError(
        f"No oracle price source wired up for asset {oracle_asset}. "
        "Populate this function with a real price feed fetch before running."
    )


def prepare(pool_address: str, oracle_asset: str, vault_start: str, vault_end: str) -> pd.DataFrame:
    swaps = fetch_pool_swaps(pool_address, vault_start, vault_end)
    oracle = fetch_oracle_prices(oracle_asset, vault_start, vault_end)

    merged = pd.merge_asof(
        swaps.sort_values("timestamp"),
        oracle.sort_values("timestamp")[["timestamp", "price"]].rename(columns={"price": "oracle_price"}),
        on="timestamp",
        direction="nearest",
    )

    start = pd.Timestamp(vault_start)
    end = pd.Timestamp(vault_end)
    merged = merged[(merged["timestamp"] >= start) & (merged["timestamp"] <= end)].reset_index(drop=True)

    return merged[
        ["timestamp", "drain_volume_usd", "pool_price", "oracle_price", "is_drain", "fee_income_usd"]
    ]


def main() -> None:
    p = argparse.ArgumentParser(description="Prepare swap + oracle data for a Saffron vault backtest")
    p.add_argument("--pool-address", required=True, help="Uniswap V3 pool contract address")
    p.add_argument("--oracle-asset", required=True, help="Asset symbol for the price feed (ETH, NVDA, etc.)")
    p.add_argument("--vault-start", required=True, help="Vault start datetime (ISO format)")
    p.add_argument("--vault-end", required=True, help="Vault end datetime (ISO format)")
    p.add_argument("--out", required=True, help="Output CSV path")
    args = p.parse_args()

    merged = prepare(args.pool_address, args.oracle_asset, args.vault_start, args.vault_end)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_path, index=False)
    print(f"Wrote {len(merged)} rows to {out_path}")


if __name__ == "__main__":
    main()
