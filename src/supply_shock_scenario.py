"""
Supply-shock / unbacked-mint-and-dump depeg scenario.

ASSUMPTION UNDER TEST
---------------------
This scenario models attackers with **zero cost basis** (compromised mint keys,
unbacked token creation). They are not capital-constrained rebalancing arbitrageurs.
LVR-style fee defenses are expected to underperform here because:

  - The attacker's marginal cost of tokens sold is ~$0, not (peg − market) spread.
  - Payoff is dominated by pool slippage on thin liquidity, not oracle-vs-pool gap.
  - Fee escalation (single-digit bps) is negligible vs multi-million-dollar extractions.

This is structurally distinct from organic CEX–DEX arbitrage depegs modeled in
`scenario.DepegScenario` and replayed via `scripts/backtest_mainnet.py`.

SLIPPAGE MODEL (LIMITATION)
---------------------------
Uses a **constant-product (x·y = k) full-range stable pool** with equal USD reserves
on each side. Real Uniswap v3 concentrated liquidity would produce different
curves; calibrate `pool_liquidity_depth` against observed realized proceeds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .oscillon_fee import BASE_FEE_BPS, FeeModel, select_fee_bps

FeeMode = Literal["static", "dynamic"]


@dataclass
class SupplyShockScenario:
    """
    Parameterize a mint-and-dump attack against a thin stable pool.

    mint_face_value:     USD face value of unbacked tokens minted and dumped.
    dump_blocks:           1 = single-block dump; N > 1 spreads evenly over N steps.
    pool_liquidity_depth: USD reserve on *each* side at peg (x = y = depth).
    initial_dev_bps:       Oracle-reported depeg at attack start (fee tier input).
    escalate_dev_with_pool: If True, fee tier uses max(oracle, pool-implied depeg).
    token_sold_is_token1:  True → sell USDT (token1) for USDC; False → sell USDC.
    fee_model:             Oscillon hybrid schedule (reuses oscillon_fee.py).
    static_fee_bps:        Flat baseline fee when fee_mode == "static".
    """

    mint_face_value: float
    dump_blocks: int = 1
    pool_liquidity_depth: float = 3_830_000.0
    initial_dev_bps: float = 6.0
    escalate_dev_with_pool: bool = True
    token_sold_is_token1: bool = True
    fee_model: FeeModel = "hybrid"
    static_fee_bps: float = BASE_FEE_BPS

    def __post_init__(self) -> None:
        if self.mint_face_value <= 0:
            raise ValueError("mint_face_value must be positive")
        if self.dump_blocks < 1:
            raise ValueError("dump_blocks must be >= 1")
        if self.pool_liquidity_depth <= 0:
            raise ValueError("pool_liquidity_depth must be positive")


@dataclass
class DumpTrade:
    block: int
    amount_in_usd: float
    fee_bps: float
    fee_usd: float
    usdc_out_usd: float
    dev_bps: float
    pool_price: float
    reserve_usdc: float
    reserve_usdt: float


@dataclass
class SupplyShockResult:
    scenario: SupplyShockScenario
    fee_mode: FeeMode
    trades: list[DumpTrade] = field(default_factory=list)
    gross_proceeds_usd: float = 0.0
    total_fees_paid_usd: float = 0.0
    net_attacker_payoff_usd: float = 0.0
    lp_fee_income_usd: float = 0.0
    lp_net_loss_usd: float = 0.0
    remaining_mint_unsold_usd: float = 0.0
    final_pool_price: float = 1.0
    max_dev_bps: float = 0.0


def pool_price_usdt_per_usdc(reserve_usdc: float, reserve_usdt: float) -> float:
    """token1/token0 spot price (USDT per USDC)."""
    if reserve_usdc <= 0:
        return float("inf")
    return reserve_usdt / reserve_usdc


def pool_dev_bps(reserve_usdc: float, reserve_usdt: float) -> float:
    price = pool_price_usdt_per_usdc(reserve_usdc, reserve_usdt)
    return abs(1.0 - price) * 10_000.0


def _fee_for_trade(
    dev_bps: float,
    is_drain: bool,
    fee_mode: FeeMode,
    scenario: SupplyShockScenario,
) -> float:
    if fee_mode == "static":
        return scenario.static_fee_bps
    return select_fee_bps(dev_bps, is_drain, fee_model=scenario.fee_model, k_override=45)


def _swap_sell_token1_for_usdc(
    reserve_usdc: float,
    reserve_usdt: float,
    amount_in_usd: float,
    fee_bps: float,
) -> tuple[float, float, float]:
    """
    Constant-product sell of token1 (USDT) into pool for token0 (USDC).

    Returns (usdc_out, fee_usd, new_reserve_usdc, new_reserve_usdt).
    """
    fee_usd = amount_in_usd * fee_bps / 10_000.0
    amount_in_after_fee = amount_in_usd - fee_usd
    if amount_in_after_fee <= 0 or reserve_usdt <= 0 or reserve_usdc <= 0:
        return 0.0, fee_usd, reserve_usdc, reserve_usdt

    usdc_out = reserve_usdc * amount_in_after_fee / (reserve_usdt + amount_in_after_fee)
    usdc_out = min(usdc_out, reserve_usdc * 0.999999)  # cannot drain more than reserve

    new_usdc = reserve_usdc - usdc_out
    new_usdt = reserve_usdt + amount_in_after_fee
    return usdc_out, fee_usd, new_usdc, new_usdt


def _swap_sell_token0_for_usdt(
    reserve_usdc: float,
    reserve_usdt: float,
    amount_in_usd: float,
    fee_bps: float,
) -> tuple[float, float, float, float]:
    """Sell token0 (USDC) for token1 (USDT); proceeds valued in USDT ≈ USD."""
    fee_usd = amount_in_usd * fee_bps / 10_000.0
    amount_in_after_fee = amount_in_usd - fee_usd
    if amount_in_after_fee <= 0 or reserve_usdt <= 0 or reserve_usdc <= 0:
        return 0.0, fee_usd, reserve_usdc, reserve_usdt

    usdt_out = reserve_usdt * amount_in_after_fee / (reserve_usdc + amount_in_after_fee)
    usdt_out = min(usdt_out, reserve_usdt * 0.999999)

    new_usdc = reserve_usdc + amount_in_after_fee
    new_usdt = reserve_usdt - usdt_out
    return usdt_out, fee_usd, new_usdc, new_usdt


def simulate_supply_shock(
    scenario: SupplyShockScenario,
    *,
    fee_mode: FeeMode = "dynamic",
) -> SupplyShockResult:
    """
    Replay a mint-and-dump against a CPAMM stable pool.

    Attacker payoff:
      net = sum(pool_payouts) − sum(fees_on_input)

    LP net loss (zero-cost-basis attacker):
      gross extraction − fee income retained by LPs
    """
    depth = scenario.pool_liquidity_depth
    reserve_usdc = depth
    reserve_usdt = depth

    chunk = scenario.mint_face_value / scenario.dump_blocks
    remaining = scenario.mint_face_value
    trades: list[DumpTrade] = []
    gross = 0.0
    fees = 0.0
    max_dev = 0.0

    for block in range(scenario.dump_blocks):
        if remaining <= 0:
            break

        amount_in = min(chunk, remaining)
        pool_dev = pool_dev_bps(reserve_usdc, reserve_usdt)
        if scenario.escalate_dev_with_pool:
            dev = max(scenario.initial_dev_bps, pool_dev)
        else:
            dev = scenario.initial_dev_bps

        # Selling the below-peg token into the pool → drain direction for fee tier.
        is_drain = True
        fee_bps = _fee_for_trade(dev, is_drain, fee_mode, scenario)

        if scenario.token_sold_is_token1:
            out_usd, fee_usd, reserve_usdc, reserve_usdt = _swap_sell_token1_for_usdc(
                reserve_usdc, reserve_usdt, amount_in, fee_bps
            )
        else:
            out_usd, fee_usd, reserve_usdc, reserve_usdt = _swap_sell_token0_for_usdt(
                reserve_usdc, reserve_usdt, amount_in, fee_bps
            )

        price = pool_price_usdt_per_usdc(reserve_usdc, reserve_usdt)
        trades.append(
            DumpTrade(
                block=block,
                amount_in_usd=amount_in,
                fee_bps=fee_bps,
                fee_usd=fee_usd,
                usdc_out_usd=out_usd,
                dev_bps=dev,
                pool_price=price,
                reserve_usdc=reserve_usdc,
                reserve_usdt=reserve_usdt,
            )
        )

        gross += out_usd
        fees += fee_usd
        remaining -= amount_in
        max_dev = max(max_dev, dev)

    lp_fee_income = fees
    # Attacker keeps all USDC out; dump-side fees are zero-marginal-cost for minted tokens.
    net_payoff = gross
    lp_net_loss = gross - lp_fee_income

    return SupplyShockResult(
        scenario=scenario,
        fee_mode=fee_mode,
        trades=trades,
        gross_proceeds_usd=gross,
        total_fees_paid_usd=fees,
        net_attacker_payoff_usd=net_payoff,
        lp_fee_income_usd=lp_fee_income,
        lp_net_loss_usd=lp_net_loss,
        remaining_mint_unsold_usd=remaining,
        final_pool_price=pool_price_usdt_per_usdc(reserve_usdc, reserve_usdt),
        max_dev_bps=max_dev,
    )


def stabl_r_default() -> SupplyShockScenario:
    """
  StablR exploit (May 2026) reference parameters.

  ~$10.4M minted; ~$2.8M realized on thin liquidity (public reporting).
  pool_liquidity_depth calibrated so CPAMM single-block dump ≈ $2.8M gross
  before fees at 6 bps initial oracle depeg.
    """
    return SupplyShockScenario(
        mint_face_value=10_400_000.0,
        dump_blocks=1,
        pool_liquidity_depth=3_830_000.0,
        initial_dev_bps=6.0,
        token_sold_is_token1=True,
    )


def calibrate_pool_depth_for_target_proceeds(
    mint_face_value: float,
    target_gross_proceeds: float,
    *,
    initial_dev_bps: float = 6.0,
    static_fee_bps: float = BASE_FEE_BPS,
) -> float:
    """
    Binary-search pool depth so gross CPAMM proceeds (static fee, 1 block) ≈ target.
    Useful when anchoring to a reported realized-extraction figure.
    """
    lo, hi = 10_000.0, mint_face_value * 10.0
    for _ in range(64):
        mid = (lo + hi) / 2.0
        sc = SupplyShockScenario(
            mint_face_value=mint_face_value,
            dump_blocks=1,
            pool_liquidity_depth=mid,
            initial_dev_bps=initial_dev_bps,
            static_fee_bps=static_fee_bps,
        )
        gross = simulate_supply_shock(sc, fee_mode="static").gross_proceeds_usd
        if gross < target_gross_proceeds:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0
