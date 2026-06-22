#!/usr/bin/env python3
"""Fetch Chainlink oracle (Dune) + Uniswap minute data (demeter-fetch BigQuery)."""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


POOL = "0x3416cF6C708Da44DB2624D63ea0AAef7113527C6"
DUNE_SQL = """
SELECT
    call_block_time AS minute,
    output_0 / 1e8 AS usdc_price
FROM chainlink_ethereum.EACAggregatorProxy_v2_call_latestAnswer
WHERE contract_address = 0x8fffffd4afb6115b954bd326cbe7b4ba576818f6
AND call_success = true
AND call_block_time >= TIMESTAMP '{start_date}'
AND call_block_time < TIMESTAMP '{end_exclusive}'
ORDER BY call_block_time
"""


def _dune_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _date_chunks(start: str, end: str, chunk_days: int) -> Iterator[tuple[str, str]]:
    """Yield (chunk_start, chunk_end_inclusive) for Dune queries."""
    cur = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while cur <= last:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), last)
        yield cur.isoformat(), chunk_end.isoformat()
        cur = chunk_end + timedelta(days=1)


def _end_exclusive(chunk_end_inclusive: str) -> str:
    """Dune filter: include all of chunk_end day via < next midnight."""
    return (date.fromisoformat(chunk_end_inclusive) + timedelta(days=1)).isoformat()


def fetch_dune_chainlink(
    api_key: str,
    start_date: str,
    end_date: str,
    out_csv: Path,
    *,
    chunk_days: int = 7,
    execute_timeout: int = 300,
    poll_timeout: int = 120,
) -> None:
    session = _dune_session()
    headers = {"X-DUNE-API-KEY": api_key}
    frames: list[pd.DataFrame] = []

    chunks = list(_date_chunks(start_date, end_date, chunk_days))
    print(f"Dune oracle: {len(chunks)} chunk(s), {chunk_days} day(s) each")

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        end_ex = _end_exclusive(chunk_end)
        sql = DUNE_SQL.format(start_date=chunk_start, end_exclusive=end_ex)
        print(f"  Chunk {i}/{len(chunks)}: {chunk_start} → {chunk_end} ...")

        execute_resp = session.post(
            "https://api.dune.com/api/v1/sql/execute",
            headers=headers,
            json={"sql": sql, "performance": "medium"},
            timeout=execute_timeout,
        )
        if not execute_resp.ok:
            raise RuntimeError(
                f"Dune execute failed ({execute_resp.status_code}): {execute_resp.text[:400]}"
            )
        execution_id = execute_resp.json()["execution_id"]

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
            time.sleep(5)

        result_resp = session.get(
            f"https://api.dune.com/api/v1/execution/{execution_id}/results",
            headers=headers,
            timeout=poll_timeout,
        )
        result_resp.raise_for_status()
        rows = result_resp.json()["result"]["rows"]
        if rows:
            frames.append(pd.DataFrame(rows))
        print(f"    got {len(rows)} rows")

    if not frames:
        raise RuntimeError("Dune returned no oracle rows for the date range")

    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["minute"])
    df = df.sort_values("minute")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"Saved {len(df)} oracle price records to {out_csv}")


def write_bigquery_config(
    *,
    start_date: str,
    end_date: str,
    save_path: Path,
    auth_file: str,
    config_path: Path,
) -> None:
    try:
        import toml
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "toml package missing. Run: python3 -m pip install -r requirements.txt"
        ) from exc

    config = {
        "from": {
            "chain": "ethereum",
            "datasource": "big_query",
            "dapp_type": "uniswap",
            "start": start_date,
            "end": end_date,
            "uniswap": {"pool_address": POOL},
            "big_query": {"auth_file": auth_file},
        },
        "to": {
            "type": "minute",
            "save_path": str(save_path),
            "skip_existed": True,
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(toml.dumps(config))


def _dune_key_from_env() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("DUNE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch oracle + Uniswap swaps (BigQuery mode)")
    p.add_argument("--start-date", default="2023-03-10")
    p.add_argument("--end-date", default="2023-03-15")
    p.add_argument("--oracle-out", default="data/chainlink_usdc_2023.csv")
    p.add_argument("--skip-dune", action="store_true", help="Skip Chainlink oracle fetch")
    p.add_argument("--dune-only", action="store_true", help="Fetch oracle only, skip BigQuery")
    p.add_argument("--dune-api-key", default="", help="Dune API key")
    p.add_argument(
        "--dune-chunk-days",
        type=int,
        default=7,
        help="Split Dune oracle fetch into N-day chunks (avoids timeouts)",
    )
    p.add_argument("--pool-out-dir", default="data")
    p.add_argument(
        "--bigquery-auth-file",
        default="",
        help="Absolute path to Google BigQuery service-account json",
    )
    p.add_argument(
        "--config-out",
        default="data/demeter_fetch_uniswap_bigquery.toml",
        help="Where to write generated demeter-fetch config",
    )
    args = p.parse_args()

    dune_key = args.dune_api_key or _dune_key_from_env()
    if not args.skip_dune:
        if not dune_key:
            raise SystemExit(
                "Missing Dune key. Set DUNE_API_KEY in .env, pass --dune-api-key, or use --skip-dune."
            )
        fetch_dune_chainlink(
            api_key=dune_key,
            start_date=args.start_date,
            end_date=args.end_date,
            out_csv=Path(args.oracle_out),
            chunk_days=max(1, args.dune_chunk_days),
        )

    if args.dune_only:
        return

    if not args.bigquery_auth_file:
        raise SystemExit("--bigquery-auth-file is required unless --dune-only or --skip-dune with no BQ")

    save_path = Path(args.pool_out_dir)
    config_path = Path(args.config_out)
    write_bigquery_config(
        start_date=args.start_date,
        end_date=args.end_date,
        save_path=save_path,
        auth_file=args.bigquery_auth_file,
        config_path=config_path,
    )

    demeter = Path(__file__).resolve().parents[1] / ".venv/bin/demeter-fetch"
    cmd = [str(demeter) if demeter.exists() else "demeter-fetch", "-c", str(config_path)]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"Done. Minute CSV files are in: {save_path}")
    print(f"Config written: {config_path}")


if __name__ == "__main__":
    main()
