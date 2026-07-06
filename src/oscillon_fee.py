"""
Oscillon dynamic fee model — mirrors OscillonFeePolicy.sol + OscillonHook.sol.

Units: on-chain / select_fee_pips use *pips* (100 pips = 1 bps).
Backtests may call select_fee_bps() directly in basis points.

Architecture (matches Solidity):
  total_fee = BASE_FEE_BPS (3) + depeg_surcharge_bps
  surcharge curves are anchored at 1 bps (not 3).
  depeg < SMALL_DEPEG_BPS (3) or non-drain → base fee only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FeeModel = Literal["piecewise", "quadratic", "hybrid", "additive"]
SurchargeModel = Literal["piecewise", "quadratic", "hybrid"]

BASE_FEE_BPS = 3.0
BASE_FEE_PIPS = int(BASE_FEE_BPS * 100)
RESTORE_FEE_PIPS = BASE_FEE_PIPS
MAX_FEE_PIPS = 5000
MAX_FEE_BPS = 50.0
SMALL_DEPEG_BPS = 3
THIN_POOL_LIQUIDITY = 500_000 * 10**6

QUADRATIC_DEAD_BAND = 3
QUADRATIC_K_DEFAULT = 45
QUADRATIC_K_THIN = 60


@dataclass(frozen=True)
class FeeContext:
    depeg_bps: int
    is_drain: bool
    pool_liquidity: int = 10**18
    using_fallback: bool = False
    in_restore_window: bool = False
    k_override: int | None = None
    fee_model: FeeModel = "hybrid"
    surcharge_model: SurchargeModel = "hybrid"


def _piecewise_surcharge_bps_int(dev_bps: float) -> int:
    """Integer surcharge — matches OscillonFeePolicy.sol piecewiseFeeBps."""
    dev = int(dev_bps)
    if dev <= QUADRATIC_DEAD_BAND:
        return 1
    if dev <= 20:
        excess = dev - QUADRATIC_DEAD_BAND
        return (10_000 + 204 * excess * excess) // 10_000
    fee_at_20 = 10_000 + 204 * 17 * 17
    linear = 11 * (dev - 20) * 100
    return min((fee_at_20 + linear) // 10_000, int(MAX_FEE_BPS))


def _quadratic_surcharge_bps_int(
    dev_bps: float,
    k: int = QUADRATIC_K_DEFAULT,
    using_fallback: bool = False,
) -> int:
    d = max(0, int(dev_bps) - QUADRATIC_DEAD_BAND)
    quad = 1 + (k * d * d) // 10_000
    quad = min(quad, int(MAX_FEE_BPS))
    if using_fallback and dev_bps < 15:
        increase = quad - 1 if quad > 1 else 0
        quad = 1 + increase // 2
    return quad


def _hybrid_surcharge_bps_int(
    dev_bps: float,
    k: int = QUADRATIC_K_DEFAULT,
    using_fallback: bool = False,
) -> int:
    if dev_bps == 0:
        return 1
    return max(
        _piecewise_surcharge_bps_int(dev_bps),
        _quadratic_surcharge_bps_int(dev_bps, k, using_fallback),
    )


def _surcharge_for_model_int(
    dev_bps: float,
    k: int,
    fee_model: FeeModel,
    using_fallback: bool,
) -> int:
    if fee_model == "piecewise":
        return _piecewise_surcharge_bps_int(dev_bps)
    if fee_model == "quadratic":
        return _quadratic_surcharge_bps_int(dev_bps, k, using_fallback)
    return _hybrid_surcharge_bps_int(dev_bps, k, using_fallback)


def select_fee_pips_hook(ctx: FeeContext) -> int:
    """On-chain integer fee path (base + surcharge pips)."""
    if not ctx.is_drain:
        if ctx.in_restore_window and ctx.depeg_bps == 0:
            return RESTORE_FEE_PIPS
        return BASE_FEE_PIPS

    if ctx.depeg_bps < SMALL_DEPEG_BPS:
        return BASE_FEE_PIPS

    k = ctx.k_override if ctx.k_override is not None else quadratic_k(ctx.pool_liquidity)
    surcharge_bps = _surcharge_for_model_int(
        ctx.depeg_bps, k, ctx.fee_model, ctx.using_fallback
    )
    total_bps = min(int(BASE_FEE_BPS) + surcharge_bps, int(MAX_FEE_BPS))
    return min(total_bps * 100, MAX_FEE_PIPS)


def _piecewise_surcharge_bps(dev_bps: float) -> float:
    """Surcharge only — float math for smooth curves; pips path rounds at conversion."""
    d = float(dev_bps)
    if d <= QUADRATIC_DEAD_BAND:
        return 1.0

    if d <= 20:
        excess = d - QUADRATIC_DEAD_BAND
        fee_x10000 = 10_000.0 + 204.0 * excess * excess
        return fee_x10000 / 10_000.0

    fee_at_20_x10000 = 10_000.0 + 204.0 * 17.0 * 17.0
    linear_x10000 = 11.0 * (d - 20.0) * 100.0
    total_x10000 = fee_at_20_x10000 + linear_x10000
    return min(total_x10000 / 10_000.0, MAX_FEE_BPS)


def _quadratic_surcharge_bps(
    dev_bps: float,
    k: int = QUADRATIC_K_DEFAULT,
    using_fallback: bool = False,
) -> float:
    """Surcharge only — float quadratic leg."""
    d = max(0.0, float(dev_bps) - QUADRATIC_DEAD_BAND)
    quad = 1.0 + (k * d * d) / 10_000.0
    quad = min(quad, MAX_FEE_BPS)

    if using_fallback and dev_bps < 15:
        increase = max(0.0, quad - 1.0)
        quad = 1.0 + increase / 2.0

    return quad


def _hybrid_surcharge_bps(
    dev_bps: float,
    k: int = QUADRATIC_K_DEFAULT,
    using_fallback: bool = False,
) -> float:
    """Surcharge only — max(piecewise, quadratic)."""
    if dev_bps == 0:
        return 1.0
    pw = _piecewise_surcharge_bps(dev_bps)
    quad = _quadratic_surcharge_bps(dev_bps, k, using_fallback)
    return max(pw, quad)


def drain_surcharge_bps(
    dev_bps: float,
    *,
    k: int = QUADRATIC_K_DEFAULT,
    surcharge_model: SurchargeModel = "hybrid",
    using_fallback: bool = False,
) -> float:
    """Depeg surcharge above base fee (0 when below SMALL_DEPEG_BPS)."""
    if dev_bps < SMALL_DEPEG_BPS:
        return 0.0

    if surcharge_model == "piecewise":
        return _piecewise_surcharge_bps(dev_bps)
    if surcharge_model == "quadratic":
        return _quadratic_surcharge_bps(dev_bps, k, using_fallback)
    return _hybrid_surcharge_bps(dev_bps, k, using_fallback)


def _surcharge_for_model(
    dev_bps: float,
    k: int,
    fee_model: FeeModel,
    surcharge_model: SurchargeModel,
    using_fallback: bool,
) -> float:
    if fee_model == "piecewise":
        return _piecewise_surcharge_bps(dev_bps)
    if fee_model == "quadratic":
        return _quadratic_surcharge_bps(dev_bps, k, using_fallback)
    if fee_model in ("hybrid", "additive"):
        return _hybrid_surcharge_bps(dev_bps, k, using_fallback)
    return _hybrid_surcharge_bps(dev_bps, k, using_fallback)


def total_fee_bps(dev_bps: float, is_drain: bool) -> float:
    """Base + surcharge for drain swaps; base only otherwise."""
    if not is_drain or dev_bps < SMALL_DEPEG_BPS:
        return BASE_FEE_BPS
    return BASE_FEE_BPS + drain_surcharge_bps(dev_bps)


def select_fee_bps(
    dev_bps: float,
    is_drain: bool,
    k_override: int | None = None,
    fee_model: FeeModel = "hybrid",
    using_fallback: bool = False,
    surcharge_model: SurchargeModel = "hybrid",
) -> float:
    if not is_drain:
        return BASE_FEE_BPS

    if dev_bps < SMALL_DEPEG_BPS:
        return BASE_FEE_BPS

    k = k_override if k_override is not None else QUADRATIC_K_DEFAULT
    surcharge = _surcharge_for_model(
        dev_bps, k, fee_model, surcharge_model, using_fallback
    )
    return min(BASE_FEE_BPS + surcharge, MAX_FEE_BPS)


def quadratic_k(pool_liquidity: int) -> int:
    return QUADRATIC_K_THIN if pool_liquidity < THIN_POOL_LIQUIDITY else QUADRATIC_K_DEFAULT


def select_fee_pips(ctx: FeeContext) -> int:
    return select_fee_pips_hook(ctx)


def fee_bps(pips: int) -> float:
    return pips / 100.0


def oscillon_fee_bps(
    depeg_bps: int,
    is_drain: bool,
    k: int | None = None,
    *,
    pool_liquidity: int = 10**18,
    fee_model: FeeModel = "hybrid",
) -> float:
    ctx = FeeContext(
        depeg_bps=int(depeg_bps),
        is_drain=bool(is_drain),
        pool_liquidity=pool_liquidity,
        k_override=k,
        fee_model=fee_model,
    )
    return fee_bps(select_fee_pips(ctx))


def depeg_from_price(price_token1_per_token0: float) -> tuple[int, bool]:
    if price_token1_per_token0 <= 0:
        return 0, False
    peg_below = price_token1_per_token0 < 1.0
    depeg_bps = int(abs(1.0 - price_token1_per_token0) * 10_000)
    return depeg_bps, peg_below


if __name__ == "__main__":
    print(f"{'dev_bps':>8} {'base':>8} {'hybrid':>8} {'additive':>10} {'tax':>8}")
    print("-" * 50)
    for d in [0, 3, 5, 7, 10, 15, 20, 25, 30, 50, 100]:
        hyb = select_fee_bps(d, True, fee_model="hybrid")
        add = select_fee_bps(d, True, fee_model="additive")
        tax = drain_surcharge_bps(d)
        print(f"{d:>8} {BASE_FEE_BPS:>8.2f} {hyb:>8.2f} {add:>10.2f} {tax:>8.2f}")
