"""
Reconstruct the real LP token inventory of a Uniswap v4 pool at every swap by
replaying ModifyLiquidity + Swap events in (block, log index) order.

Why: swap events alone cannot give LP "reserves" — LPs mint, burn and re-center
between swaps, so cumulative swap flow is not inventory. A concentrated position
with liquidity L on [tickLower, tickUpper] holds, at sqrt price s (clamped to the
range), token0 = L*(1/s - 1/sb) and token1 = L*(s - sa) in raw units. Summing over
all live positions gives the pool's principal inventory (fees excluded).

Built-in check: the Swap event reports the active liquidity after the swap. The
replayed active liquidity must match it, or the position set / ordering is wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

Q96 = 2**96
_HALF_LN_TICK = math.log(1.0001) / 2.0


def sqrt_ratio_at_tick(tick: int) -> float:
    return math.exp(tick * _HALF_LN_TICK)


def position_amounts(
    sqrt_p: float, sa: np.ndarray, sb: np.ndarray, liq: np.ndarray
) -> tuple[float, float]:
    """Total raw (token0, token1) principal across positions at sqrt price sqrt_p."""
    s = np.clip(sqrt_p, sa, sb)
    return float((liq * (1.0 / s - 1.0 / sb)).sum()), float((liq * (s - sa)).sum())


@dataclass
class ReplayResult:
    frame: pd.DataFrame
    n_positions: int
    negative_liquidity_events: int
    liquidity_match_rate: float


def replay(
    swaps: pd.DataFrame,
    mods: pd.DataFrame,
    init_sqrt_price_x96: int,
    decimals0: int,
    decimals1: int,
) -> ReplayResult:
    """
    swaps: blk, idx, ts, amount0, amount1, sqrtPriceX96, tick, liquidity (ints as str/int);
           amount0/amount1 are the raw v4 event values (swapper perspective)
    mods:  blk, idx, sender, salt, tickLower, tickUpper, liquidityDelta
    Returns a per-swap frame with token-unit inventory before/after each swap.
    """
    s_blk = swaps["blk"].to_numpy(dtype=np.int64)
    s_idx = swaps["idx"].to_numpy(dtype=np.int64)
    s_a0 = np.array([int(x) for x in swaps["amount0"]], dtype=object)
    s_a1 = np.array([int(x) for x in swaps["amount1"]], dtype=object)
    s_sqrt = [int(x) for x in swaps["sqrtPriceX96"]]
    s_tick = swaps["tick"].to_numpy(dtype=np.int64)
    s_liq = [int(x) for x in swaps["liquidity"]]

    m_blk = mods["blk"].to_numpy(dtype=np.int64)
    m_idx = mods["idx"].to_numpy(dtype=np.int64)
    m_key = list(zip(mods["sender"], mods["salt"], mods["tickLower"].astype(int), mods["tickUpper"].astype(int)))
    m_delta = [int(x) for x in mods["liquidityDelta"]]

    cap = 4096
    tl = np.zeros(cap, dtype=np.int64)
    tu = np.zeros(cap, dtype=np.int64)
    sa = np.ones(cap)
    sb = np.ones(cap)
    liq = np.zeros(cap)
    liq_int: list[int] = []
    index: dict[tuple, int] = {}
    n_pos = 0
    neg_events = 0

    n = len(swaps)
    d0, d1 = 10.0**decimals0, 10.0**decimals1
    v0_pre = np.empty(n)
    v1_pre = np.empty(n)
    v0_post = np.empty(n)
    v1_post = np.empty(n)
    liq_replay = np.empty(n)
    a0 = np.empty(n)
    a1 = np.empty(n)

    sqrt_cur = init_sqrt_price_x96 / Q96
    j = 0
    n_mods = len(mods)
    dirty = True  # inventory at current price needs recomputing
    cur_v0 = cur_v1 = 0.0

    for i in range(n):
        while j < n_mods and (m_blk[j], m_idx[j]) < (s_blk[i], s_idx[i]):
            key = m_key[j]
            k = index.get(key)
            if k is None:
                if n_pos == cap:
                    cap *= 2
                    tl = np.resize(tl, cap); tu = np.resize(tu, cap)
                    sa = np.resize(sa, cap); sb = np.resize(sb, cap); liq = np.resize(liq, cap)
                k = n_pos
                n_pos += 1
                index[key] = k
                liq_int.append(0)
                tl[k], tu[k] = key[2], key[3]
                sa[k], sb[k] = sqrt_ratio_at_tick(key[2]), sqrt_ratio_at_tick(key[3])
            liq_int[k] += m_delta[j]
            if liq_int[k] < 0:
                neg_events += 1
            liq[k] = float(liq_int[k])
            dirty = True
            j += 1

        if dirty:
            x, y = position_amounts(sqrt_cur, sa[:n_pos], sb[:n_pos], liq[:n_pos])
            cur_v0, cur_v1 = x / d0, y / d1
            dirty = False
        v0_pre[i], v1_pre[i] = cur_v0, cur_v1

        sqrt_cur = s_sqrt[i] / Q96
        x, y = position_amounts(sqrt_cur, sa[:n_pos], sb[:n_pos], liq[:n_pos])
        cur_v0, cur_v1 = x / d0, y / d1
        v0_post[i], v1_post[i] = cur_v0, cur_v1

        active = (tl[:n_pos] <= s_tick[i]) & (s_tick[i] < tu[:n_pos])
        liq_replay[i] = liq[:n_pos][active].sum()
        # Uniswap v4 Swap-event amounts are from the SWAPPER's perspective (positive =
        # swapper receives = token leaves the pool). Convert to pool perspective so
        # a > 0 means the token ENTERS the pool. (Verified: price-up swaps have
        # event amount0 > 0 in 100% of cases, and a raw receipt shows token0
        # transferred into the PoolManager while the event reports amount0 < 0.)
        a0[i] = -float(s_a0[i]) / d0
        a1[i] = -float(s_a1[i]) / d1

    liq_event = np.array([float(x) for x in s_liq])
    match = np.abs(liq_replay - liq_event) <= 1e-9 * np.maximum(1.0, np.abs(liq_event))

    frame = pd.DataFrame(
        {
            "ts": pd.to_datetime(swaps["ts"].astype(str).str.replace(" UTC", "", regex=False)),
            "blk": s_blk, "idx": s_idx,
            "a0": a0, "a1": a1,
            "v0_pre": v0_pre, "v1_pre": v1_pre,
            "v0_post": v0_post, "v1_post": v1_post,
            "liq_event": liq_event, "liq_replay": liq_replay,
            "tick": s_tick,
        }
    )
    return ReplayResult(frame=frame, n_positions=n_pos, negative_liquidity_events=neg_events,
                        liquidity_match_rate=float(match.mean()) if n else 1.0)
