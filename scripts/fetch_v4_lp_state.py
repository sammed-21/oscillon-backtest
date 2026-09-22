#!/usr/bin/env python3
"""
Fetch everything needed to replay a Uniswap v4 pool's LP inventory:
  - swaps (with block/log ordering keys and exact sqrtPriceX96)
  - liquidity modify events (per-position liquidityDelta + tick range)
  - the Initialize event (starting price)

Swap events alone cannot tell you LP inventory: LPs mint, burn and re-center
positions between swaps. Replaying ModifyLiquidity + Swap in (block, log index)
order reconstructs the real token composition of the liquidity at every swap.

Outputs (gzip CSV, big ints kept as strings) go to --out-dir.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fetch_data import _date_chunks, _end_exclusive  # noqa: E402

SWAP_SQL = """
SELECT evt_block_time AS ts, evt_block_number AS blk, evt_index AS idx,
       amount0, amount1, sqrtPriceX96, tick, liquidity
FROM uniswap_v4_{chain}.PoolManager_evt_Swap
WHERE id = {pool_id}
AND evt_block_time >= TIMESTAMP '{start}' AND evt_block_time < TIMESTAMP '{end}'
ORDER BY evt_block_number, evt_index
"""

MODIFY_SQL = """
SELECT evt_block_time AS ts, evt_block_number AS blk, evt_index AS idx,
       sender, salt, tickLower, tickUpper, liquidityDelta
FROM uniswap_v4_{chain}.PoolManager_evt_ModifyLiquidity
WHERE id = {pool_id}
AND evt_block_time >= TIMESTAMP '{start}' AND evt_block_time < TIMESTAMP '{end}'
ORDER BY evt_block_number, evt_index
"""

INIT_SQL = """
SELECT evt_block_time AS ts, evt_block_number AS blk, evt_index AS idx,
       currency0, currency1, fee, tickSpacing, sqrtPriceX96, tick
FROM uniswap_v4_{chain}.PoolManager_evt_Initialize
WHERE id = {pool_id}
"""

COUNT_SQL = """
SELECT COUNT(*) AS n FROM uniswap_v4_{chain}.PoolManager_evt_{table}
WHERE id = {pool_id}
AND evt_block_time >= TIMESTAMP '{start}' AND evt_block_time < TIMESTAMP '{end}'
"""


def _session() -> requests.Session:
    retry = Retry(total=5, connect=5, read=5, backoff_factor=2,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


class Dune:
    def __init__(self, api_key: str):
        self.s = _session()
        self.h = {"X-DUNE-API-KEY": api_key}

    def rows(self, sql: str) -> list[dict]:
        r = self.s.post("https://api.dune.com/api/v1/sql/execute", headers=self.h,
                        json={"sql": sql, "performance": "medium"}, timeout=300)
        if not r.ok:
            raise RuntimeError(f"Dune execute failed ({r.status_code}): {r.text[:300]}")
        eid = r.json()["execution_id"]
        while True:
            st = self.s.get(f"https://api.dune.com/api/v1/execution/{eid}/status", headers=self.h, timeout=120)
            st.raise_for_status()
            state = st.json().get("state")
            if state == "QUERY_STATE_COMPLETED":
                break
            if state in {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"}:
                raise RuntimeError(f"Dune query failed: {st.text[:300]}")
            time.sleep(3)
        res = self.s.get(f"https://api.dune.com/api/v1/execution/{eid}/results", headers=self.h, timeout=120)
        res.raise_for_status()
        return res.json()["result"]["rows"]


def fetch_chunked(dune: Dune, sql_tpl: str, chain: str, pool_id: str, start: str, end: str, chunk_days: int, label: str) -> pd.DataFrame:
    chunks = list(_date_chunks(start, end, chunk_days))
    frames = []
    for i, (a, b) in enumerate(chunks, 1):
        rows = dune.rows(sql_tpl.format(chain=chain, pool_id=pool_id, start=a, end=_end_exclusive(b)))
        print(f"  [{label}] chunk {i}/{len(chunks)} {a}..{b}: {len(rows)} rows", flush=True)
        if rows:
            frames.append(pd.DataFrame(rows))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch v4 swaps + liquidity events for LP inventory replay")
    p.add_argument("--pool-id", required=True)
    p.add_argument("--chain", default="ethereum")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--swap-chunk-days", type=int, default=7)
    p.add_argument("--modify-chunk-days", type=int, default=30)
    args = p.parse_args()

    key = os.environ.get("DUNE_API_KEY", "")
    if not key:
        raise SystemExit("Set DUNE_API_KEY in the environment / .env")
    dune = Dune(key)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    init = dune.rows(INIT_SQL.format(chain=args.chain, pool_id=args.pool_id))
    if not init:
        raise SystemExit("No Initialize event found for this pool id on this chain")
    init = init[0]
    start = str(init["ts"])[:10]
    print(f"Pool initialized {init['ts']} at tick {init['tick']}; fetching {start} -> {args.end}", flush=True)

    swaps = fetch_chunked(dune, SWAP_SQL, args.chain, args.pool_id, start, args.end, args.swap_chunk_days, "swaps")
    mods = fetch_chunked(dune, MODIFY_SQL, args.chain, args.pool_id, start, args.end, args.modify_chunk_days, "modify")

    meta = {"pool_id": args.pool_id, "chain": args.chain, "start": start, "end": args.end,
            "init": {k: str(v) for k, v in init.items()}}
    end_ex = _end_exclusive(args.end)
    for name, df, table in (("swaps", swaps, "Swap"), ("modify", mods, "ModifyLiquidity")):
        n_before = len(df)
        df = df.drop_duplicates(subset=["blk", "idx"]).sort_values(["blk", "idx"]).reset_index(drop=True)
        truth = dune.rows(COUNT_SQL.format(chain=args.chain, pool_id=args.pool_id, table=table, start=start, end=end_ex))[0]["n"]
        print(f"{name}: fetched {n_before}, unique {len(df)}, ground truth now {truth}, diff {len(df) - truth:+d}", flush=True)
        meta[f"{name}_rows"] = len(df)
        meta[f"{name}_dropped_dupes"] = n_before - len(df)
        meta[f"{name}_ground_truth"] = int(truth)
        with gzip.open(out / f"{name}.csv.gz", "wt") as f:
            df.to_csv(f, index=False)
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
