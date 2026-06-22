"""Load demeter-format minute CSVs from data/."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .swap_direction import TOKEN0_SYMBOL, TOKEN1_SYMBOL
from .uniswap_math import tick_to_price

DEFAULT_POOL = "0x3416cf6c708da44db2624d63ea0aaef7113527c6"
DEFAULT_CHAIN = "ethereum"


def load_minute_range(
    data_dir: str | Path,
    chain: str,
    pool_address: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    data_dir = Path(data_dir)
    chain = chain.lower()
    pool_address = pool_address.lower()
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
    return _enrich(df)


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("inAmount0", "inAmount1", "netAmount0", "netAmount1"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(np.float64)

    df["closeTick"] = df["closeTick"].astype(int)
    df["price"] = df["closeTick"].apply(tick_to_price)
    df["token0"] = TOKEN0_SYMBOL
    df["token1"] = TOKEN1_SYMBOL
    df["volume_usd"] = (df["inAmount0"].abs() + df["inAmount1"].abs()) / 1e6
    # USDC sold into pool (token0 net inflow) — direction filter before oracle merge
    df["usdc_sold_into_pool"] = df["netAmount0"] > 0
    df["drain_volume_usd"] = np.where(df["usdc_sold_into_pool"], df["volume_usd"], 0.0)
    return df
