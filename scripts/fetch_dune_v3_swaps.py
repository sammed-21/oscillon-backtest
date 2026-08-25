#!/usr/bin/env python3
"""
Fetch Uniswap v3 swap history via Dune's raw event logs (not the decoded
per-pool tables, which have shown indexing gaps for some contracts this
session) and aggregate to minute bars, same schema as the v4 fetch script.

v3 Swap event: Swap(address indexed sender, address indexed recipient,
int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity,
int24 tick). Unlike v4, each pool is its own contract — filter by
contract_address directly, no PoolId needed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fetch_data import _date_chunks, _end_exclusive  # noqa: E402

SWAP_TOPIC0 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

DUNE_V3_SWAP_SQL = """
SELECT block_time AS timestamp, data
FROM ethereum.logs
WHERE contract_address = {pool_address}
AND topic0 = {topic0}
AND block_time >= TIMESTAMP '{start_date}'
AND block_time < TIMESTAMP '{end_exclusive}'
ORDER BY block_time
"""


def _session() -> requests.Session:
    retry = Retry(total=5, connect=5, read=5, backoff_factor=2,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _decode_data(hex_data: str) -> tuple[int, int, int]:
    data = hex_data[2:]
    words = [data[i:i + 64] for i in range(0, len(data), 64)]

    def to_signed(h: str) -> int:
        v = int(h, 16)
        return v - 2**256 if v >= 2**255 else v

    amount0 = to_signed(words[0])
    amount1 = to_signed(words[1])
    tick_raw = int(words[4], 16)
    tick = tick_raw - 2**256 if tick_raw >= 2**255 else tick_raw
    return amount0, amount1, tick


def fetch_v3_swaps_raw(api_key: str, pool_address: str, start_date: str, end_date: str, *, chunk_days: int = 5) -> pd.DataFrame:
    session = _session()
    headers = {"X-DUNE-API-KEY": api_key}
    chunks = list(_date_chunks(start_date, end_date, chunk_days))
    print(f"Dune v3 swaps ({pool_address}): {len(chunks)} chunk(s), {chunk_days} day(s) each")

    rows: list[dict] = []
    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        end_ex = _end_exclusive(chunk_end)
        sql = DUNE_V3_SWAP_SQL.format(pool_address=pool_address, topic0=SWAP_TOPIC0, start_date=chunk_start, end_exclusive=end_ex)
        print(f"  Chunk {i}/{len(chunks)}: {chunk_start} -> {chunk_end} ...")

        exec_resp = session.post("https://api.dune.com/api/v1/sql/execute", headers=headers,
                                  json={"sql": sql, "performance": "medium"}, timeout=300)
        if not exec_resp.ok:
            raise RuntimeError(f"Dune execute failed ({exec_resp.status_code}): {exec_resp.text[:400]}")
        execution_id = exec_resp.json()["execution_id"]

        while True:
            status_resp = session.get(f"https://api.dune.com/api/v1/execution/{execution_id}/status",
                                       headers=headers, timeout=120)
            status_resp.raise_for_status()
            state = status_resp.json().get("state")
            if state == "QUERY_STATE_COMPLETED":
                break
            if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"}:
                raise RuntimeError(f"Dune query failed: {status_resp.text[:400]}")
            time.sleep(4)

        result_resp = session.get(f"https://api.dune.com/api/v1/execution/{execution_id}/results",
                                   headers=headers, timeout=120)
        result_resp.raise_for_status()
        chunk_rows = result_resp.json()["result"]["rows"]
        print(f"    got {len(chunk_rows)} rows")
        for r in chunk_rows:
            amount0, amount1, tick = _decode_data(r["data"])
            rows.append({"timestamp": r["timestamp"], "amount0": amount0, "amount1": amount1, "tick": tick})

    return pd.DataFrame(rows, columns=["timestamp", "amount0", "amount1", "tick"])


def aggregate_to_minute(swaps: pd.DataFrame) -> pd.DataFrame:
    if swaps.empty:
        return pd.DataFrame(columns=["timestamp", "netAmount0", "netAmount1", "closeTick", "openTick",
                                      "lowestTick", "highestTick", "inAmount0", "inAmount1", "currentLiquidity"])
    df = swaps.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    df["amount0"] = df["amount0"].apply(int)
    df["amount1"] = df["amount1"].apply(int)
    df["tick"] = df["tick"].astype(int)
    df = df.sort_values("timestamp")
    df["minute"] = df["timestamp"].dt.floor("min")

    grouped = df.groupby("minute", sort=True)
    out = grouped.agg(
        netAmount0=("amount0", "sum"), netAmount1=("amount1", "sum"),
        closeTick=("tick", "last"), openTick=("tick", "first"),
        lowestTick=("tick", "min"), highestTick=("tick", "max"),
    )
    out["inAmount0"] = grouped["amount0"].apply(lambda s: s[s > 0].sum())
    out["inAmount1"] = grouped["amount1"].apply(lambda s: s[s > 0].sum())
    out["currentLiquidity"] = 0
    out = out.reset_index().rename(columns={"minute": "timestamp"})
    cols = ["timestamp", "netAmount0", "netAmount1", "closeTick", "openTick",
            "lowestTick", "highestTick", "inAmount0", "inAmount1", "currentLiquidity"]
    return out[cols]


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch Uniswap v3 swaps via Dune raw logs, aggregate to minute bars")
    p.add_argument("--pool-address", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--dune-api-key", default="")
    p.add_argument("--chunk-days", type=int, default=5)
    args = p.parse_args()

    api_key = args.dune_api_key
    if not api_key:
        import os
        api_key = os.environ.get("DUNE_API_KEY", "")
    if not api_key:
        raise SystemExit("No Dune API key: pass --dune-api-key or set DUNE_API_KEY in env/.env")

    raw = fetch_v3_swaps_raw(api_key, args.pool_address, args.start, args.end, chunk_days=args.chunk_days)
    minute = aggregate_to_minute(raw)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"v3_swaps_raw_{args.start}_{args.end}.csv"
    raw.to_csv(raw_path, index=False)
    print(f"Saved {len(raw)} raw swap rows to {raw_path}")

    if minute.empty:
        print("No swaps in range — no minute file written.")
        return
    for day, day_df in minute.groupby(minute["timestamp"].dt.date):
        day_path = out_dir / f"v3-{args.pool_address.lower()}-{day}.minute.csv"
        day_df.to_csv(day_path, index=False)
    print(f"Saved {minute['timestamp'].dt.date.nunique()} daily minute files to {out_dir}")


if __name__ == "__main__":
    main()
