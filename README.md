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

## Verified event replays

```bash
python3 scripts/run_verified_backtests.py   # March 2023 USDC (in-repo data)
python3 -m pytest tests/ -q
```

Results: `output/verified_backtest_results.md`

| Event | Prepared data | Status |
|-------|---------------|--------|
| USDC Mar 2023 (SVB) | `data/prepared_swaps_2023-03.csv` | ✅ in repo |
| USDe Oct 2025 | `data/prepared_swaps_usde_2025-10.csv` | fetch BigQuery first |


- Data source: `backtest_mainnet.py` only (not `depeg_analysis.py` for headline numbers)
- Base fee: 3 bps (`BASE_FEE_BPS = 3.0`)
- LVR formula: `max(0, drain_size × (dev_bps − fee_bps) / 10000)` on drain swaps only
- Volume loss: drain swaps where `dynamic_fee > competitor_fee` **and** `dev_bps < 50`
- Swap size: drain leg only (`netAmount0.clip(lower=0) / 1e6` for USDC/USDT pool)
- APR: annualised period return × `(365 / days)` — label as **stress period APR**, not normal APR
- Solidity parity: Python uses identical base + surcharge architecture as `OscillonFeePolicy.sol`

### Publishing limitations (conservative direction)

These are documented, accepted biases — they understate surcharge/LP income, not overstate it:

| Limitation | Effect on headline numbers |
|------------|---------------------------|
| Some swap replays from Infura RPC, not BigQuery | +10% drain volume → ~0.71 → ~0.78 bps/year (immaterial) |
| Backtest uses hook integer fees, not float | Slightly lower LP income (e.g. dev=7: 4.0 vs 4.33 bps) |
| Oracle `merge_asof` backward, no staleness cap | Rare gaps understate `dev_bps` during fast depegs |

### USDC vs USDT oracle legs (separate — never mixed)

| Leg | Oracle feed | Drain rule | Use for |
|-----|-------------|------------|---------|
| **USDC** (`token0`) | Chainlink USDC/USD | `netAmount0 > 0` + peg below | **Deployed hook / auditor headlines** |
| **USDT** (`token1`) | Chainlink USDT/USD | `netAmount1 > 0` + peg below | Counterfactual only — not deployed |

Run both legs end-to-end (separate prepared CSVs, charts, timelines):

```bash
python3 scripts/run_oracle_leg_pipeline.py --start 2026-01-01 --end 2026-06-30
```

Outputs:
- `data/prepared_swaps_2026_h1_usdc_oracle.csv` + `output/backtest_2026_h1_usdc_oracle.png`
- `data/prepared_swaps_2026_h1_usdt_oracle.csv` + `output/backtest_2026_h1_usdt_oracle.png`
- `output/oracle_leg_comparison_2026_h1.md`

Single leg only:

```bash
python3 scripts/run_oracle_leg_pipeline.py --leg usdc --start 2026-01-01 --end 2026-06-30
python3 scripts/run_oracle_leg_pipeline.py --leg usdt --start 2026-01-01 --end 2026-06-30
```

Fetch USDT oracle (once):

```bash
python3 scripts/fetch_data.py --dune-only --oracle-asset usdt \
  --start-date 2026-01-01 --end-date 2026-06-30 \
  --oracle-out data/chainlink_usdt_2026_h1.csv
```

## Cross-asset research (new stables / RWAs)

Pool presets in `src/pool_config.py`:

| Preset | Pool | Chain | Use |
|--------|------|-------|-----|
| `usdc-usdt` | USDC/USDT | Ethereum | Deployed baseline |
| `usde-usdt` | USDe/USDT v4 | Ethereum | Active ~$4.5M pool |
| `usde-usdt-legacy` | USDe/USDT v3 | Ethereum | Oct 2025 minute files in repo |
| `pyusd-usdc` | PYUSD/USDC v4 | Ethereum | Thin fiat stable |
| `fdusd-usdc-bsc` | FDUSD/USDC | BSC | Apr 2025 sentiment depeg |

Fetch oracles (Dune):

```bash
python3 scripts/fetch_data.py --dune-only --oracle-asset usde --start-date 2026-01-01 --end-date 2026-06-30
python3 scripts/fetch_data.py --dune-only --oracle-asset pyusd --start-date 2026-01-01 --end-date 2026-06-30
python3 scripts/fetch_data.py --dune-only --oracle-asset fdusd --start-date 2025-04-01 --end-date 2025-04-05 \
  --oracle-out data/chainlink_fdusd_2025-04.csv
```

NAV reference mode (RWAs — OUSG, USDY):

```bash
python3 scripts/prepare_data.py \
  --pool-preset pyusd-usdc \
  --oracle-source nav \
  --nav-csv data/nav_ousg_sample.csv \
  --reference-mode nav \
  --out data/prepared_swaps_ousg_nav_sample.csv
```

Cross-asset scorecard (runs backtests on prepared CSVs that exist):

```bash
python3 scripts/asset_scorecard.py
# → output/asset_scorecard.md + output/asset_scorecard.json
```

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
