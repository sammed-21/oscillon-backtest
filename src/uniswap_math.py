"""Minimal Uniswap v3 tick ↔ price helpers for stable/stable pools."""

from __future__ import annotations

import math


def tick_to_price(token1_per_token0: int) -> float:
    return 1.0001**token1_per_token0


def price_to_tick(price: float) -> int:
    if price <= 0:
        raise ValueError("price must be positive")
    return int(math.log(price) / math.log(1.0001))
