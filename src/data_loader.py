"""Load demeter-format minute CSVs from data/."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .pool_config import USDC_USDT, PoolConfig, get_pool_config
from .swap_direction import classify_prepared_swaps
from .uniswap_math import tick_to_price

DEFAULT_POOL = USDC_USDT.address
DEFAULT_CHAIN = "ethereum"


def load_minute_range(
    data_dir: str | Path,
    chain: str,
    pool_address: str,
    start: date,
    end: date,
    *,
    pool: PoolConfig | None = None,
) -> pd.DataFrame:
    pool = pool or get_pool_config(pool_address)
    data_dir = Path(data_dir)
    chain = chain.lower()
    pool_address = pool.address.lower()
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        path = data_dir / f"{chain}-{pool_address}-{day:%Y-%m-%d}.minute.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. Fetch swaps first: see README.md (fetch-ethers)."
            )
        frames.append(pd.read_csv(path))
        day += timedelta(days=1)

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").set_index("timestamp")
    return _enrich(df, pool)


def _enrich(df: pd.DataFrame, pool: PoolConfig = USDC_USDT) -> pd.DataFrame:
    for col in ("inAmount0", "inAmount1", "netAmount0", "netAmount1"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(np.float64)

    df["closeTick"] = df["closeTick"].astype(int)
    df["price"] = df["closeTick"].apply(tick_to_price)
    df["pool_price"] = df["price"]
    classified = classify_prepared_swaps(df.reset_index(), pool=pool).set_index("timestamp")
    for col in (
        "token0",
        "token1",
        "pool_address",
        "volume_usd",
        "drain_size_usd",
        "restore_size_usd",
        "swap_size_usd",
        "drain_volume_usd",
        "swap_direction",
        "is_drain",
        "peg_below",
        "token0_sold_into_pool",
        "usdc_sold_into_pool",
    ):
        if col in classified.columns:
            df[col] = classified[col]
    df["volume_usd"] = df.get("swap_size_usd", 0.0)
    return df
