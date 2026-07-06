#!/usr/bin/env python3
"""Re-prepare and replay canonical event backtests (USDC Mar 2023, USDe Oct 2025)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("\n$", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    py = sys.executable

    # --- March 2023 USDC (SVB stress) ---
    run(
        [
            py,
            "scripts/prepare_data.py",
            "--use-minute-files",
            "--pool-preset",
            "usdc-usdt",
            "--start",
            "2023-03-10",
            "--end",
            "2023-03-15",
            "--oracle",
            "data/chainlink_usdc_2023.csv",
            "--out",
            "data/prepared_swaps_2023-03.csv",
        ]
    )
    run(
        [
            py,
            "scripts/backtest_mainnet.py",
            "--prepared",
            "data/prepared_swaps_2023-03.csv",
            "--chart-out",
            "output/oscillon_backtest_2023-03.png",
            "--timeline-out",
            "output/depeg_fee_timeline_2023-03.png",
            "--timeline-csv",
            "output/depeg_fee_timeline_2023-03.csv",
        ]
    )

    # --- Oct 2025 USDe (on-chain volatility; pool-price oracle) ---
    usde_files = list(
        ROOT.glob("data/ethereum-0x435664008f38b0650fbc1c9fc971d0a3bc2f1e47-2025-10-*.minute.csv")
    )
    if not usde_files:
        print(
            "\nUSDe minute CSVs not found for Oct 2025. Fetch with:\n"
            "  python3 scripts/fetch_data.py --pool-preset usde-usdt-legacy "
            "--start-date 2025-10-10 --end-date 2025-10-12 --skip-dune\n"
            "Then re-run this script."
        )
    else:
        run(
            [
                py,
                "scripts/prepare_data.py",
                "--use-minute-files",
                "--pool-preset",
                "usde-usdt-legacy",
                "--start",
                "2025-10-10",
                "--end",
                "2025-10-12",
                "--oracle-source",
                "pool",
                "--out",
                "data/prepared_swaps_usde_2025-10.csv",
            ]
        )
        run(
            [
                py,
                "scripts/backtest_mainnet.py",
                "--prepared",
                "data/prepared_swaps_usde_2025-10.csv",
                "--chart-out",
                "output/oscillon_backtest_usde_2025-10.png",
                "--timeline-out",
                "output/depeg_fee_timeline_usde_2025-10.png",
                "--timeline-csv",
                "output/depeg_fee_timeline_usde_2025-10.csv",
            ]
        )

    print("\nVerified backtests complete.")


if __name__ == "__main__":
    main()
