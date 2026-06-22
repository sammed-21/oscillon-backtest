# Oscillon fee backtest

Compare **static 3 bps** vs **Oscillon dynamic drain fee** on Ethereum USDC/USDT minute swaps.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

## Data

Minute CSVs in `data/` named:

`ethereum-0x3416cf6c708da44db2624d63ea0aaef7113527c6-YYYY-MM-DD.minute.csv`

Fetch with ethers (no BigQuery):

```bash
cd fetch-ethers && npm install && npm run fetch -- --chain ethereum --hours 24
```

Set `ETHEREUM_RPC_URL` in `fetch-ethers/.env` if needed. Output lands in `../data/`.

## Backtest

```bash
python3 scripts/backtest.py --start 2023-03-10 --end 2023-03-15 \
  --mode oracle --oracle-csv data/chainlink_usdc_2023.csv --fee-model hybrid
python3 scripts/backtest.py --start 2023-03-10 --end 2023-03-15 --apr
```

Writes `output/summary.json`. Stress window (default): Chainlink USDC $0.997, pool lags 15 bps for 180 minutes.

## APR / APY

**Fee APR/APY** = annualized swap-fee income (positive yield).  
**Net APR/APY** = fee yield minus modeled LVR; negative under stress when toxic flow &gt; fees.

```bash
python3 scripts/calc_apr.py --days 1 --capital 100000 --tvl 2500000
```

## Fee model

| Case | Fee |
|------|-----|
| Healthy / restore | 3 bps base |
| Drain, depeg &lt; 3 bps | 3 bps base only |
| Drain, depeg ≥ 3 bps | 3 bps base + hybrid surcharge (piecewise ∩ quadratic, anchor 1 bps) |

Solidity parity: `BASE_FEE_PIPS = 300` + `OscillonFeePolicy.hybridFeeBps()` surcharge.

## Backtest Methodology Notes

- Data source: `backtest_mainnet.py` only (not `depeg_analysis.py` for headline numbers)
- Base fee: 3 bps (`BASE_FEE_BPS = 3.0`)
- LVR formula: `max(0, drain_size × (dev_bps − fee_bps) / 10000)` on drain swaps only
- Volume loss: drain swaps where `dynamic_fee > competitor_fee` **and** `dev_bps < 50`
- Swap size: drain leg only (`netAmount0.clip(lower=0) / 1e6` for USDC/USDT pool)
- APR: annualised period return × `(365 / days)` — label as **stress period APR**, not normal APR
- Solidity parity: Python uses identical base + surcharge architecture as `OscillonFeePolicy.sol`

## Tests

```bash
python3 -m pytest tests/ -q
```

## Layout

```
src/oscillon_fee.py   # hook-aligned fee
src/depeg_analysis.py # static vs dynamic + LVR proxy
src/scenario.py       # inject stress depeg
src/data_loader.py    # load minute CSVs
src/apr.py            # APR/APY
scripts/backtest.py
scripts/calc_apr.py
fetch-ethers/         # optional data fetch
```

## ETH Mainnet Pipeline (BigQuery)

Uses Chainlink USDC oracle from Dune + `demeter-fetch` with BigQuery for swaps (no RPC calls).

**Full month (example: May 2026):**

```bash
export BIGQUERY_AUTH_FILE=/absolute/path/to/gcp-bigquery.json
chmod +x scripts/run_month_pipeline.sh
./scripts/run_month_pipeline.sh 2026-05
```

Or step by step:

```bash
# 1) Fetch oracle + 31 daily *.minute.csv files (Dune key from .env if set)
python3 scripts/fetch_data.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-31 \
  --oracle-out data/chainlink_usdc_2026-05.csv \
  --bigquery-auth-file "$BIGQUERY_AUTH_FILE"

# 2) Merge oracle, dev_bps, is_drain (peg_below & USDC sold into pool)
python3 scripts/prepare_data.py \
  --use-minute-files \
  --start 2026-05-01 --end 2026-05-31 \
  --oracle data/chainlink_usdc_2026-05.csv \
  --out data/prepared_swaps_2026-05.csv

# 3) Backtest + charts
python3 scripts/backtest_mainnet.py --prepared data/prepared_swaps_2026-05.csv
python3 scripts/compare_fee_models.py --prepared data/prepared_swaps_2026-05.csv
```

Minute files land in `data/` as:
`ethereum-0x3416cf6c708da44db2624d63ea0aaef7113527c6-2026-05-DD.minute.csv`

Equivalent generated demeter-fetch config:

```bash
cat data/demeter_fetch_uniswap_bigquery.toml
```

## Optimize K (λ score)

```bash
python3 scripts/sweep_k.py --prepared data/prepared_swaps.csv --ks 20,30,45,60,80
```

Score: `lp_revenue - λ1×LVR - λ2×volume_loss`. Best K is printed and saved to `output/k_sweep_score.png`.
Use that K in the hook / `FeeContext(..., k_override=K)`.

If you already have oracle CSV and only want swaps:

```bash
python3 scripts/fetch_data.py --skip-dune \
  --start-date 2023-03-10 \
  --end-date 2023-03-15 \
  --bigquery-auth-file /absolute/path/to/gcp-bigquery.json
```
