"""Known Uniswap stable pools for backtest replay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PoolConfig:
    address: str
    token0_symbol: str
    token1_symbol: str
    token0_decimals: int = 6
    token1_decimals: int = 6
    oracle_asset_symbol: str = ""
    chain: str = "ethereum"
    description: str = ""

    @property
    def oracle_symbol(self) -> str:
        return self.oracle_asset_symbol or self.token0_symbol


USDC_USDT = PoolConfig(
    address="0x3416cf6c708da44db2624d63ea0aaef7113527c6",
    token0_symbol="USDC",
    token1_symbol="USDT",
    oracle_asset_symbol="USDC",
    description="Ethereum USDC/USDT 0.01% — SVB stress Mar 2023",
)

USDE_USDT = PoolConfig(
    address="0x63bb22f47c7ede6578a25c873e77eb782ec8e4c19778e36ce64d37877b5bd1e7",
    token0_symbol="USDe",
    token1_symbol="USDT",
    token0_decimals=18,
    token1_decimals=6,
    oracle_asset_symbol="USDe",
    description="Ethereum USDe/USDT v4 pool (PoolId, not a contract address — "
    "v4 has no per-pool contracts). Swap data fetched via Dune's decoded "
    "PoolManager_evt_Swap table (scripts/fetch_dune_v4_swaps.py); minute files "
    "in data/usde_usdt/. Token0/token1 order and decimals verified via tick-to-"
    "price sanity check (~$1.00), 2025-10-02 to 2026-08-20.",
)

USDE_USDT_LEGACY = PoolConfig(
    address="0x435664008f38b0650fbc1c9fc971d0a3bc2f1e47",
    token0_symbol="USDe",
    token1_symbol="USDT",
    token0_decimals=18,
    token1_decimals=6,
    oracle_asset_symbol="USDe",
    description="Ethereum USDe/USDT v3 pool — candidate v3 fallback, liquidity "
    "not yet verified. Superseded by fetching the real v4 pool (USDE_USDT) "
    "directly via Dune; kept here in case a v3 comparison is ever needed.",
)

USDS_USDT = PoolConfig(
    address="0x3b1b1f2e775a6db1664f8e7d59ad568605ea2406312c11aef03146c0cf89d5b9",
    token0_symbol="USDT",
    token1_symbol="USDS",
    token0_decimals=6,
    token1_decimals=18,
    oracle_asset_symbol="USDS",
    description="Ethereum USDT/USDS v4 pool (PoolId), fee tier 5 (0.05 bps), "
    "tick spacing 1. Dominant pool by volume among 18 USDS/USDT v4 pools "
    "checked (175,611 swaps since 2026-01-24 vs next-highest 19,670) — "
    "confirmed ~$50M TVL per Uniswap UI. currency0=USDT/currency1=USDS per "
    "on-chain Initialize event (USDT address sorts lower). No confirmed "
    "Chainlink USDS/USD feed found; use --oracle-source pool.",
)

PYUSD_USDS = PoolConfig(
    address="0xe63e32b2ae40601662f760d6bf5d771057324fbd97784fe1d3717069f7b75d45",
    token0_symbol="PYUSD",
    token1_symbol="USDS",
    token0_decimals=6,
    token1_decimals=18,
    oracle_asset_symbol="PYUSD",
    description="Ethereum PYUSD/USDS v4 pool (PoolId), fee tier 5 (0.05 bps), "
    "tick spacing 1. currency0=PYUSD (verified via symbol()/decimals() RPC "
    "calls), currency1=USDS. Active since 2026-02-19, 28,149+ swaps.",
)

USDE_USDC = PoolConfig(
    address="0x56fc29b86900aa0afa6e20b020429bffba1105cfb45070492c67529b46eb48c1",
    token0_symbol="USDe",
    token1_symbol="USDC",
    token0_decimals=18,
    token1_decimals=6,
    oracle_asset_symbol="USDe",
    description="Ethereum USDe/USDC v4 pool (PoolId). ~$612k TVL per Uniswap UI "
    "(2026-08); only pool with meaningful liquidity for this pair — all v3 "
    "candidates are $63 TVL or less. Pool created 2026-07-17, so history is "
    "only ~5 weeks, not a year. Swap data in data/usde_usdc/, fetched via Dune. "
    "Token0/token1 order and decimals verified via tick-to-price sanity check.",
)

AUSD_USDC_MONAD = PoolConfig(
    address="0x092b650478145f0aee73a1b400b342b9c6314db2e07aeb91faf7e75e8159ce72",
    token0_symbol="AUSD",
    token1_symbol="USDC",
    token0_decimals=6,
    token1_decimals=6,
    oracle_asset_symbol="AUSD",
    chain="monad",
    description="Monad mainnet AUSD/USDC v4 pool (PoolId), fee tier 50 "
    "(0.5 bps), tick spacing 1. Initialized 2025-11-19; 292,928+ swaps by "
    "2026-08. First cross-chain (non-Ethereum) pool tested. No independent "
    "oracle found — Chainlink has no price feeds on Monad yet (only CCIP "
    "bridge infra), RedStone's multi-feed adapter table exists but is "
    "unindexed. Uses --oracle-source pool (same limitation as USDS/USDT's "
    "first pass): no cross-venue confirmation of depeg vs. pool-specific noise.",
)

FRAX_USDC = PoolConfig(
    address="0xc63b0708e2f7e69cb8a1df0e1389a98c35a76d52",
    token0_symbol="FRAX",
    token1_symbol="USDC",
    token0_decimals=18,
    token1_decimals=6,
    oracle_asset_symbol="FRAX",
    description="Ethereum FRAX/USDC v3 pool (real contract address, not a "
    "v4 PoolId). Legacy fractional-algorithmic FRAX (pre-2023 FXIP-188 "
    "vote), NOT frxUSD (which is fully collateralized). Active continuously "
    "since 2021. Chainlink FRAX/USD feed traced via Feed Registry "
    "(0x8f73090a7c58b8bdcc9a93cbb6816e5cc4f01e8c) — updates far more "
    "frequently (18,799 events since mid-2024) than fully-collateralized "
    "peers, consistent with genuine higher volatility.",
)

PYUSD_USDC = PoolConfig(
    address="0xa2a5a544a8cbd9c24557b8393fd909360779cf0f48a0b88895a7d9d83ce9d437",
    token0_symbol="PYUSD",
    token1_symbol="USDC",
    oracle_asset_symbol="PYUSD",
    description="Ethereum PYUSD/USDC v4 0.01% — thin liquidity, fiat-backed",
)

FDUSD_USDC_BSC = PoolConfig(
    address="0xc5f0f7b66764f6ec8c8dff7ba683102295e16409",
    token0_symbol="FDUSD",
    token1_symbol="USDC",
    token0_decimals=18,
    token1_decimals=18,
    oracle_asset_symbol="FDUSD",
    chain="bnb",
    description="BSC PancakeSwap FDUSD/USDC — Apr 2025 sentiment depeg",
)

POOL_PRESETS: dict[str, PoolConfig] = {
    "usdc-usdt": USDC_USDT,
    "usde-usdt": USDE_USDT,
    "usde-usdt-legacy": USDE_USDT_LEGACY,
    "usde-usdc": USDE_USDC,
    "usds-usdt": USDS_USDT,
    "pyusd-usds": PYUSD_USDS,
    "frax-usdc": FRAX_USDC,
    "ausd-usdc-monad": AUSD_USDC_MONAD,
    "pyusd-usdc": PYUSD_USDC,
    "fdusd-usdc-bsc": FDUSD_USDC_BSC,
}


def get_pool_config(preset_or_address: str) -> PoolConfig:
    key = preset_or_address.lower().strip()
    if key in POOL_PRESETS:
        return POOL_PRESETS[key]
    addr = key if key.startswith("0x") else f"0x{key}"
    for cfg in POOL_PRESETS.values():
        if cfg.address.lower() == addr:
            return cfg
    raise ValueError(
        f"Unknown pool {preset_or_address!r}. "
        f"Use preset {list(POOL_PRESETS)} or a known address."
    )
