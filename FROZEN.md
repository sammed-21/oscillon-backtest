# Frozen

This repository is the original record of the Oscillon stablecoin depeg research (oracle-triggered hybrid fee,
LVR-proxy capture metric). It is kept as is so its published numbers stay reproducible.

- Do not run or edit anything here.
- All new work lives in `oscillon-markout/research/markout-backtest/`, which reads this repo's data files read-only.
- The published Mar 2023 capture figures reconcile as follows (checked against this repo's own engine):
  33.2% = on-chain whole-bp rounding with the 50 bp cap in force before commit 27d97ab (Jul 30, 2026);
  34.3% = float curve, 50 bp cap; 38.8% / 39.9% = the same two paths with today's 100 bp cap.
  See `oscillon-markout/research/markout-backtest/REPORT_MARKOUT_RESEARCH.md`, section 6.
