"""Asset research case registry."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.asset_research import RESEARCH_CASES, case_by_id


def test_research_cases_non_empty():
    assert len(RESEARCH_CASES) >= 5


def test_usdc_baseline_present():
    case = case_by_id("usdc_stress_mar23")
    assert case.category == "baseline"
    assert case.reference_mode == "dollar_peg"


def test_rwa_nav_cases():
    usdy = case_by_id("usdy_nav_sample")
    assert usdy.oracle_source == "nav"
    ousg = case_by_id("ousg_nav_sample")
    assert ousg.reference_mode == "nav"
