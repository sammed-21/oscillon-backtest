"""Cross-asset research presets for Oscillon backtest scorecard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .pool_config import (
    FDUSD_USDC_BSC,
    PYUSD_USDC,
    USDC_USDT,
    USDE_USDT,
    USDE_USDT_LEGACY,
    PoolConfig,
)


@dataclass(frozen=True)
class AssetResearchCase:
    """One row in the cross-asset comparison table."""

    asset_id: str
    label: str
    pool_preset: str
    pool: PoolConfig
    prepared_csv: str
    oracle_source: str  # chainlink | pool | nav
    oracle_file: str
    reference_mode: str  # dollar_peg | nav
    period_label: str
    category: str  # baseline | new_stable | rwa | counterfactual
    fetch_hint: str = ""
    partnership_tier: str = ""  # primary | secondary | skip


RESEARCH_CASES: tuple[AssetResearchCase, ...] = (
    AssetResearchCase(
        asset_id="usdc_calm_h1",
        label="USDC/USDT calm H1 2026",
        pool_preset="usdc-usdt",
        pool=USDC_USDT,
        prepared_csv="data/prepared_swaps_2026_h1_usdc_oracle.csv",
        oracle_source="chainlink",
        oracle_file="data/chainlink_usdc_2026_h1.csv",
        reference_mode="dollar_peg",
        period_label="2026 H1",
        category="baseline",
        partnership_tier="deployed",
    ),
    AssetResearchCase(
        asset_id="usdc_stress_mar23",
        label="USDC/USDT Mar 2023 SVB",
        pool_preset="usdc-usdt",
        pool=USDC_USDT,
        prepared_csv="data/prepared_swaps_2023-03.csv",
        oracle_source="chainlink",
        oracle_file="data/chainlink_usdc_2023.csv",
        reference_mode="dollar_peg",
        period_label="Mar 2023 stress",
        category="baseline",
        partnership_tier="deployed",
    ),
    AssetResearchCase(
        asset_id="usdt_cf_h1",
        label="USDT oracle leg H1 2026 (CF)",
        pool_preset="usdc-usdt",
        pool=USDC_USDT,
        prepared_csv="data/prepared_swaps_2026_h1_usdt_oracle.csv",
        oracle_source="chainlink",
        oracle_file="data/chainlink_usdt_2026_h1.csv",
        reference_mode="dollar_peg",
        period_label="2026 H1",
        category="counterfactual",
        partnership_tier="skip",
    ),
    AssetResearchCase(
        asset_id="usde_oct25",
        label="USDe/USDT Oct 2025 on-chain",
        pool_preset="usde-usdt-legacy",
        pool=USDE_USDT_LEGACY,
        prepared_csv="data/prepared_swaps_usde_2025-10.csv",
        oracle_source="pool",
        oracle_file="",
        reference_mode="dollar_peg",
        period_label="Oct 10–12 2025",
        category="new_stable",
        fetch_hint=(
            "python3 scripts/fetch_data.py --pool-preset usde-usdt-legacy "
            "--start-date 2025-10-10 --end-date 2025-10-12 --skip-dune"
        ),
        partnership_tier="primary",
    ),
    AssetResearchCase(
        asset_id="usde_v4_calm",
        label="USDe/USDT v4 calm",
        pool_preset="usde-usdt",
        pool=USDE_USDT,
        prepared_csv="data/prepared_swaps_usde_2026_h1.csv",
        oracle_source="chainlink",
        oracle_file="data/chainlink_usde_2026_h1.csv",
        reference_mode="dollar_peg",
        period_label="2026 H1",
        category="new_stable",
        fetch_hint=(
            "python3 scripts/fetch_data.py --dune-only --oracle-asset usde "
            "--start-date 2026-01-01 --end-date 2026-06-30; "
            "then fetch swaps for pool usde-usdt"
        ),
        partnership_tier="primary",
    ),
    AssetResearchCase(
        asset_id="fdusd_apr25",
        label="FDUSD/USDC Apr 2025 depeg",
        pool_preset="fdusd-usdc-bsc",
        pool=FDUSD_USDC_BSC,
        prepared_csv="data/prepared_swaps_fdusd_2025-04.csv",
        oracle_source="chainlink",
        oracle_file="data/chainlink_fdusd_2025-04.csv",
        reference_mode="dollar_peg",
        period_label="Apr 2–4 2025",
        category="new_stable",
        fetch_hint=(
            "python3 scripts/fetch_data.py --dune-only --oracle-asset fdusd "
            "--start-date 2025-04-01 --end-date 2025-04-05 "
            "--oracle-out data/chainlink_fdusd_2025-04.csv; "
            "BigQuery swaps on BSC fdusd-usdc-bsc"
        ),
        partnership_tier="primary",
    ),
    AssetResearchCase(
        asset_id="pyusd_calm",
        label="PYUSD/USDC",
        pool_preset="pyusd-usdc",
        pool=PYUSD_USDC,
        prepared_csv="data/prepared_swaps_pyusd_2026_h1.csv",
        oracle_source="chainlink",
        oracle_file="data/chainlink_pyusd_2026_h1.csv",
        reference_mode="dollar_peg",
        period_label="2026 H1",
        category="new_stable",
        fetch_hint=(
            "python3 scripts/fetch_data.py --dune-only --oracle-asset pyusd "
            "--start-date 2026-01-01 --end-date 2026-06-30"
        ),
        partnership_tier="secondary",
    ),
    AssetResearchCase(
        asset_id="usdy_nav_sample",
        label="USDY/USDC (NAV mode sample)",
        pool_preset="pyusd-usdc",
        pool=PYUSD_USDC,
        prepared_csv="data/prepared_swaps_usdy_nav_sample.csv",
        oracle_source="nav",
        oracle_file="data/nav_usdy_sample.csv",
        reference_mode="nav",
        period_label="sample",
        category="rwa",
        fetch_hint="Merge Ondo daily NAV + pool swaps; use --oracle-source nav",
        partnership_tier="primary",
    ),
    AssetResearchCase(
        asset_id="ousg_nav_sample",
        label="OUSG/USDC (NAV mode sample)",
        pool_preset="pyusd-usdc",
        pool=PYUSD_USDC,
        prepared_csv="data/prepared_swaps_ousg_nav_sample.csv",
        oracle_source="nav",
        oracle_file="data/nav_ousg_sample.csv",
        reference_mode="nav",
        period_label="sample",
        category="rwa",
        fetch_hint="NAV ~112–115 USD; drain when pool_price < NAV",
        partnership_tier="primary",
    ),
)


def case_by_id(asset_id: str) -> AssetResearchCase:
    for case in RESEARCH_CASES:
        if case.asset_id == asset_id:
            return case
    raise KeyError(f"Unknown asset_id {asset_id!r}")


def prepared_path(case: AssetResearchCase, *, root: Path | None = None) -> Path:
    base = root or Path(".")
    return base / case.prepared_csv
