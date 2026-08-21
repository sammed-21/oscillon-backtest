#!/usr/bin/env python3
"""
Systematic verification of fetched Uniswap v4 swap data, replacing one-off
manual spot checks with four repeatable checks:

1. Completeness  — fetched row count vs a fresh Dune COUNT(*) ground truth.
2. Aggregation   — minute files correctly sum the raw swap-level rows.
3. Sanity bounds — every row's implied price stays in a plausible band.
4. Random sample — N random rows independently re-derived from raw RPC
   event logs (same method used for the manual checks), match rate reported.
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DUNE_HEADERS = lambda key: {"X-DUNE-API-KEY": key}  # noqa: E731
RPC_URL = os.environ.get("ETH_RPC_URL", "")
SWAP_TOPIC0 = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
POOLMANAGER = "0x000000000004444C5dc75cB358380D2e3dE08A90"


def _dune_query(sql: str, api_key: str, poll_timeout: int = 120) -> list[dict]:
    resp = requests.post(
        "https://api.dune.com/api/v1/sql/execute",
        headers=DUNE_HEADERS(api_key),
        json={"sql": sql, "performance": "medium"},
        timeout=300,
    )
    resp.raise_for_status()
    execution_id = resp.json()["execution_id"]
    while True:
        status = requests.get(
            f"https://api.dune.com/api/v1/execution/{execution_id}/status",
            headers=DUNE_HEADERS(api_key),
            timeout=poll_timeout,
        ).json()
        state = status.get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"}:
            raise RuntimeError(f"Dune query failed: {status}")
        time.sleep(3)
    result = requests.get(
        f"https://api.dune.com/api/v1/execution/{execution_id}/results",
        headers=DUNE_HEADERS(api_key),
        timeout=poll_timeout,
    ).json()
    return result["result"]["rows"]


def check_completeness(pool_id: str, start: str, end: str, raw_row_count: int, api_key: str) -> dict:
    rows = _dune_query(
        f"SELECT COUNT(*) AS n FROM uniswap_v4_ethereum.PoolManager_evt_Swap "
        f"WHERE id = {pool_id} AND evt_block_time >= TIMESTAMP '{start}' "
        f"AND evt_block_time < TIMESTAMP '{end}'",
        api_key,
    )
    ground_truth = rows[0]["n"]
    return {
        "ground_truth_rows": ground_truth,
        "fetched_rows": raw_row_count,
        "match": ground_truth == raw_row_count,
        "diff": raw_row_count - ground_truth,
    }


def check_aggregation(raw_df: pd.DataFrame, minute_df: pd.DataFrame) -> dict:
    raw = raw_df.copy()
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_localize(None)
    raw["minute"] = raw["timestamp"].dt.floor("min")
    raw["amount0"] = raw["amount0"].apply(lambda x: int(x))
    raw["amount1"] = raw["amount1"].apply(lambda x: int(x))
    raw["tick"] = raw["tick"].astype(int)

    grouped = raw.groupby("minute", sort=True)
    recomputed = grouped.agg(
        netAmount0=("amount0", "sum"),
        netAmount1=("amount1", "sum"),
        closeTick=("tick", "last"),
        openTick=("tick", "first"),
        lowestTick=("tick", "min"),
        highestTick=("tick", "max"),
    ).reset_index().rename(columns={"minute": "timestamp"})

    m = minute_df.copy()
    m["timestamp"] = pd.to_datetime(m["timestamp"])
    # netAmount0/1 must be read as exact integers — casting through float64
    # (~15-17 sig figs) silently rounds 18-decimal amounts up to ~1e23.
    m["netAmount0"] = m["netAmount0"].apply(int)
    m["netAmount1"] = m["netAmount1"].apply(int)

    merged = recomputed.merge(m, on="timestamp", suffixes=("_recomputed", "_stored"), how="outer", indicator=True)
    only_in_one = merged[merged["_merge"] != "both"]
    both = merged[merged["_merge"] == "both"]
    mismatches = both[
        (both["netAmount0_recomputed"] != both["netAmount0_stored"])
        | (both["netAmount1_recomputed"] != both["netAmount1_stored"])
        | (both["closeTick_recomputed"] != both["closeTick_stored"])
    ]
    return {
        "total_minutes_checked": len(both),
        "minutes_only_in_one_source": len(only_in_one),
        "value_mismatches": len(mismatches),
        "clean": len(only_in_one) == 0 and len(mismatches) == 0,
    }


def check_sanity_bounds(minute_df: pd.DataFrame, decimals0: int, decimals1: int, low: float = 0.5, high: float = 2.0) -> dict:
    from src.uniswap_math import tick_to_price

    ticks = minute_df["closeTick"].astype(int)
    prices = ticks.apply(lambda t: tick_to_price(t) * (10**decimals0) / (10**decimals1))
    out_of_band = prices[(prices < low) | (prices > high)]
    return {
        "rows_checked": len(prices),
        "min_price": float(prices.min()),
        "max_price": float(prices.max()),
        "out_of_band_rows": len(out_of_band),
        "clean": len(out_of_band) == 0,
    }


def check_random_sample(pool_id: str, raw_df: pd.DataFrame, api_key: str, n: int = 15) -> dict:
    sample = raw_df.sample(min(n, len(raw_df)), random_state=42)
    results = []
    for _, row in sample.iterrows():
        ts = pd.to_datetime(row["timestamp"], utc=True)
        window_start = (ts - pd.Timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
        window_end = (ts + pd.Timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
        window_rows = _dune_query(
            f"SELECT evt_tx_hash, amount0, amount1 FROM uniswap_v4_ethereum.PoolManager_evt_Swap "
            f"WHERE id = {pool_id} AND evt_block_time >= TIMESTAMP '{window_start}' "
            f"AND evt_block_time <= TIMESTAMP '{window_end}'",
            api_key,
        )
        matches = [
            r for r in window_rows
            if int(r["amount0"]) == int(row["amount0"]) and int(r["amount1"]) == int(row["amount1"])
        ]
        if not matches:
            results.append({"timestamp": str(ts), "found_tx": False, "rpc_match": False})
            continue
        tx_hash = matches[0]["evt_tx_hash"]
        receipt = requests.post(
            RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]},
            timeout=30,
        ).json()
        logs = receipt.get("result", {}).get("logs", []) if receipt.get("result") else []
        found = False
        for log in logs:
            if log["address"].lower() != POOLMANAGER.lower():
                continue
            if not log["topics"] or log["topics"][0].lower() != SWAP_TOPIC0.lower():
                continue
            data = log["data"][2:]
            words = [data[i : i + 64] for i in range(0, len(data), 64)]

            def to_signed(h: str) -> int:
                v = int(h, 16)
                return v - 2**256 if v >= 2**255 else v

            rpc_amount0 = to_signed(words[0])
            rpc_amount1 = to_signed(words[1])
            if rpc_amount0 == int(row["amount0"]) and rpc_amount1 == int(row["amount1"]):
                found = True
                break
        results.append({"timestamp": str(ts), "tx_hash": tx_hash, "found_tx": True, "rpc_match": found})
        time.sleep(0.3)

    matches = sum(1 for r in results if r["rpc_match"])
    return {"sampled": len(results), "rpc_matches": matches, "match_rate": matches / len(results) if results else 0, "details": results}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pool-id", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--decimals0", type=int, default=18)
    p.add_argument("--decimals1", type=int, default=6)
    p.add_argument("--sample-n", type=int, default=15)
    p.add_argument("--dune-api-key", default="")
    args = p.parse_args()

    api_key = args.dune_api_key or os.environ.get("DUNE_API_KEY", "")
    if not api_key:
        raise SystemExit("Need DUNE_API_KEY")
    if not RPC_URL:
        raise SystemExit("Need ETH_RPC_URL set in environment/.env for the random-sample RPC cross-check")

    raw_files = glob.glob(str(Path(args.data_dir) / "v4_swaps_raw*.csv"))
    raw_df = pd.concat([pd.read_csv(f) for f in raw_files], ignore_index=True)

    minute_files = glob.glob(str(Path(args.data_dir) / f"v4-{args.pool_id.lower()}-*.minute.csv"))
    minute_df = pd.concat(
        [pd.read_csv(f, dtype={"netAmount0": str, "netAmount1": str}) for f in minute_files],
        ignore_index=True,
    )

    print(f"Pool {args.pool_id}: {len(raw_df)} raw rows, {len(minute_df)} minute rows across {len(minute_files)} files\n")

    print("1. COMPLETENESS")
    c1 = check_completeness(args.pool_id, args.start, args.end, len(raw_df), api_key)
    print(f"   {c1}\n")

    print("2. AGGREGATION CORRECTNESS")
    c2 = check_aggregation(raw_df, minute_df)
    print(f"   {c2}\n")

    print("3. SANITY BOUNDS")
    c3 = check_sanity_bounds(minute_df, args.decimals0, args.decimals1)
    print(f"   {c3}\n")

    print(f"4. RANDOM SAMPLE (n={args.sample_n}) — RPC cross-check")
    c4 = check_random_sample(args.pool_id, raw_df, api_key, n=args.sample_n)
    print(f"   sampled={c4['sampled']} rpc_matches={c4['rpc_matches']} match_rate={c4['match_rate']:.1%}")
    for d in c4["details"]:
        print(f"     {d}")


if __name__ == "__main__":
    main()
