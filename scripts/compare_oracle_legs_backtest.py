#!/usr/bin/env python3
"""Side-by-side backtest: USDC oracle (token0 leg) vs USDT oracle (token1 leg).

Prefer: python3 scripts/run_oracle_leg_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_oracle_leg_pipeline import main

if __name__ == "__main__":
    main()
