"""Pool config presets."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pool_config import USDC_USDT, USDE_USDT, USDE_USDT_LEGACY, get_pool_config


def test_usdc_usdt_preset():
    cfg = get_pool_config("usdc-usdt")
    assert cfg.token0_symbol == "USDC"
    assert cfg.token0_decimals == 6


def test_usde_usdt_preset():
    cfg = get_pool_config("usde-usdt")
    assert cfg.token0_symbol == "USDe"
    assert cfg.token0_decimals == 18
    assert cfg.address == USDE_USDT.address


def test_usde_legacy_preset():
    cfg = get_pool_config("usde-usdt-legacy")
    assert cfg.address == USDE_USDT_LEGACY.address


def test_pyusd_and_fdusd_presets():
    pyusd = get_pool_config("pyusd-usdc")
    assert pyusd.token0_symbol == "PYUSD"
    fdusd = get_pool_config("fdusd-usdc-bsc")
    assert fdusd.chain == "bnb"


def test_get_by_address():
    assert get_pool_config(USDC_USDT.address).token0_symbol == "USDC"
    assert get_pool_config(USDE_USDT.address).token0_symbol == "USDe"
    assert get_pool_config(USDE_USDT_LEGACY.address).token0_symbol == "USDe"
