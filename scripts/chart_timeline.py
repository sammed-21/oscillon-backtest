#!/usr/bin/env python3
"""Build depeg + fee timeline chart from prepared_swaps.csv only."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_mainnet import (  # noqa: E402
    build_timeline_chart,
    build_timeline_from_swaps,
)

if __name__ == "__main__":
    import argparse

    import pandas as pd

    p = argparse.ArgumentParser()
    p.add_argument("--prepared", default="data/prepared_swaps.csv")
    p.add_argument("--out", default="output/depeg_fee_timeline.png")
    p.add_argument("--csv-out", default="output/depeg_fee_timeline.csv")
    p.add_argument("--show", action="store_true")
    a = p.parse_args()

    swaps = pd.read_csv(a.prepared)
    timeline = build_timeline_from_swaps(swaps)
    Path(a.csv_out).parent.mkdir(parents=True, exist_ok=True)
    timeline.to_csv(a.csv_out, index=False)
    build_timeline_chart(timeline, chart_out=a.out, show_chart=a.show)
