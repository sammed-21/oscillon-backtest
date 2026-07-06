#!/usr/bin/env python3
"""Resolve Ethereum block numbers for UTC date range (binary search via RPC)."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _rpc_url() -> str:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ETH_RPC_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            if line.startswith("ETHEREUM_RPC_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ETH_RPC_URL") or os.environ.get("ETHEREUM_RPC_URL", "")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD UTC")
    p.add_argument("--end", required=True, help="YYYY-MM-DD UTC inclusive end uses next-day midnight")
    args = p.parse_args()

    try:
        from web3 import Web3
    except ImportError:
        raise SystemExit("pip install web3") from None

    rpc = _rpc_url()
    if not rpc:
        raise SystemExit("Set ETH_RPC_URL in .env")

    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        raise SystemExit(f"RPC not connected: {rpc[:40]}...")

    def ts(s: str) -> int:
        return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())

    start_ts = ts(args.start)
    end_ts = ts(args.end) + 86400  # through end date

    def block_at(target: int) -> int:
        lo, hi = 1, w3.eth.block_number
        while lo < hi:
            mid = (lo + hi) // 2
            b = w3.eth.get_block(mid)
            if b.timestamp < target:
                lo = mid + 1
            else:
                hi = mid
        return lo

    print(block_at(start_ts), block_at(end_ts))


if __name__ == "__main__":
    main()
