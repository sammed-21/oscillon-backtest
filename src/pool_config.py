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
    description="Ethereum USDe/USDT v4 0.005% — active pool (~$4.5M TVL)",
)

USDE_USDT_LEGACY = PoolConfig(
    address="0x435664008f38b0650fbc1c9fc971d0a3bc2f1e47",
    token0_symbol="USDe",
    token1_symbol="USDT",
    token0_decimals=18,
    token1_decimals=6,
    oracle_asset_symbol="USDe",
    description="Ethereum USDe/USDT legacy v3 pool — Oct 2025 minute files in repo",
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
