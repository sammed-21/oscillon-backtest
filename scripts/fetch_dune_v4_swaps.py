#!/usr/bin/env python3
"""
Fetch Uniswap v4 swap history via Dune's decoded PoolManager table and
aggregate to minute bars in the same schema as the v3 demeter-fetch minute
files (timestamp,netAmount0,netAmount1,closeTick,openTick,lowestTick,
highestTick,inAmount0,inAmount1,currentLiquidity), so downstream tooling
(prepare_data.py --use-minute-files) works unchanged.

v4 has no per-pool contract — every pool's swaps live in the shared
PoolManager contract (uniswap_v4_ethereum.PoolManager_evt_Swap), keyed by
`id` (the 32-byte PoolId), not by pool address.
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

DUNE_V4_SWAP_SQL = """
SELECT
    evt_block_time AS timestamp,
    amount0,
    amount1,
    tick,
    liquidity
FROM uniswap_v4_{chain}.PoolManager_evt_Swap
WHERE id = {pool_id}
AND evt_block_time >= TIMESTAMP '{start_date}'
AND evt_block_time < TIMESTAMP '{end_exclusive}'
ORDER BY evt_block_time
"""


def _session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch_v4_swaps_raw(
    api_key: str,
    pool_id: str,
    start_date: str,
    end_date: str,
    *,
    chain: str = "ethereum",
    chunk_days: int = 3,
    execute_timeout: int = 300,
    poll_timeout: int = 120,
) -> pd.DataFrame:
    session = _session()
    headers = {"X-DUNE-API-KEY": api_key}
    chunks = list(_date_chunks(start_date, end_date, chunk_days))
    print(f"Dune v4 swaps ({chain}, {pool_id}): {len(chunks)} chunk(s), {chunk_days} day(s) each")

    frames: list[pd.DataFrame] = []
    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        end_ex = _end_exclusive(chunk_end)
        sql = DUNE_V4_SWAP_SQL.format(chain=chain, pool_id=pool_id, start_date=chunk_start, end_exclusive=end_ex)
        print(f"  Chunk {i}/{len(chunks)}: {chunk_start} -> {chunk_end} ...")

        exec_resp = session.post(
            "https://api.dune.com/api/v1/sql/execute",
            headers=headers,
            json={"sql": sql, "performance": "medium"},
            timeout=execute_timeout,
        )
        if not exec_resp.ok:
            raise RuntimeError(f"Dune execute failed ({exec_resp.status_code}): {exec_resp.text[:400]}")
        execution_id = exec_resp.json()["execution_id"]

        while True:
            status_resp = session.get(
                f"https://api.dune.com/api/v1/execution/{execution_id}/status",
                headers=headers,
                timeout=poll_timeout,
            )
            status_resp.raise_for_status()
            state = status_resp.json().get("state")
            if state == "QUERY_STATE_COMPLETED":
                break
            if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"}:
                raise RuntimeError(f"Dune query failed: {status_resp.text[:400]}")
            print(f"    waiting... state={state}")
            time.sleep(4)

        result_resp = session.get(
            f"https://api.dune.com/api/v1/execution/{execution_id}/results",
            headers=headers,
            timeout=poll_timeout,
        )
        result_resp.raise_for_status()
        rows = result_resp.json()["result"]["rows"]
        print(f"    got {len(rows)} rows")
        if rows:
            frames.append(pd.DataFrame(rows))

    if not frames:
        return pd.DataFrame(columns=["timestamp", "amount0", "amount1", "tick", "liquidity"])
    return pd.concat(frames, ignore_index=True)


def aggregate_to_minute(swaps: pd.DataFrame) -> pd.DataFrame:
    if swaps.empty:
        return pd.DataFrame(
            columns=[
                "timestamp", "netAmount0", "netAmount1", "closeTick", "openTick",
                "lowestTick", "highestTick", "inAmount0", "inAmount1", "currentLiquidity",
            ]
        )
    df = swaps.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    # Exact Python int, not float64 — 18-decimal amounts (up to ~1e23) exceed
    # float64's ~15-17 significant digits, silently rounding large sums.
    df["amount0"] = df["amount0"].apply(int)
    df["amount1"] = df["amount1"].apply(int)
    df["tick"] = df["tick"].astype(int)
    df = df.sort_values("timestamp")
    df["minute"] = df["timestamp"].dt.floor("min")

    grouped = df.groupby("minute", sort=True)
    out = grouped.agg(
        netAmount0=("amount0", "sum"),
        netAmount1=("amount1", "sum"),
        closeTick=("tick", "last"),
        openTick=("tick", "first"),
        lowestTick=("tick", "min"),
        highestTick=("tick", "max"),
        currentLiquidity=("liquidity", "last"),
    )
    out["inAmount0"] = grouped["amount0"].apply(lambda s: s[s > 0].sum())
    out["inAmount1"] = grouped["amount1"].apply(lambda s: s[s > 0].sum())
    out = out.reset_index().rename(columns={"minute": "timestamp"})
    cols = [
        "timestamp", "netAmount0", "netAmount1", "closeTick", "openTick",
        "lowestTick", "highestTick", "inAmount0", "inAmount1", "currentLiquidity",
    ]
    return out[cols]


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch Uniswap v4 swaps via Dune, aggregate to minute bars")
    p.add_argument("--pool-id", required=True, help="v4 PoolId, 0x + 64 hex chars")
    p.add_argument("--chain", default="ethereum", help="Dune chain schema suffix, e.g. ethereum, monad")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--dune-api-key", default="", help="Falls back to DUNE_API_KEY env var")
    p.add_argument("--chunk-days", type=int, default=3)
    args = p.parse_args()

    api_key = args.dune_api_key
    if not api_key:
        import os

        api_key = os.environ.get("DUNE_API_KEY", "")
    if not api_key:
        raise SystemExit("No Dune API key: pass --dune-api-key or set DUNE_API_KEY in env/.env")

    raw = fetch_v4_swaps_raw(api_key, args.pool_id, args.start, args.end, chain=args.chain, chunk_days=args.chunk_days)
    minute = aggregate_to_minute(raw)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / f"v4_swaps_raw_{args.start}_{args.end}.csv"
    raw.to_csv(raw_path, index=False)
    print(f"Saved {len(raw)} raw swap rows to {raw_path}")

    if minute.empty:
        print("No swaps in range — no minute file written.")
        return

    for day, day_df in minute.groupby(minute["timestamp"].dt.date):
        day_path = out_dir / f"v4-{args.pool_id.lower()}-{day}.minute.csv"
        day_df.to_csv(day_path, index=False)
    print(f"Saved {minute['timestamp'].dt.date.nunique()} daily minute files to {out_dir}")


if __name__ == "__main__":
    main()
