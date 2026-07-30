# Oscillon LVR Research — Stablecoin Depeg Backtests

*Empirical research on Loss Versus Rebalancing in Uniswap stablecoin pools, and how Oscillon's dynamic fee hook addresses it.*

---

## What This Research Is

Five months of empirical work on LVR (Loss Versus Rebalancing) in Uniswap v3 USDC/USDT liquidity. The core questions: how much value do LPs lose to toxic flow during stablecoin depegs, and can a dynamic drain fee recover a meaningful share of that spread without breaking calm-regime economics?

Oscillon is a Uniswap v4 hook that charges a **hybrid dynamic surcharge** on drain-direction swaps when Chainlink shows the stablecoin below $1. Base fee is 3 bps; surcharge activates when deviation ≥ 3 bps on toxic flow.

---

## The LVR Problem

During a stablecoin depeg, arbitrageurs and panic sellers **drain** one side of the pool (sell the depegged asset into the AMM). LPs provide liquidity at a stale price and absorb adverse selection.

On each drain swap:

```
Spread available  = drain_size × depeg_bps / 10_000
LP income         = drain_size × fee_bps / 10_000
LVR (leaked)      = max(0, drain_size × (depeg_bps − fee_bps) / 10_000)
LP capture %      = LP income / (LP income + LVR)
```

With a **static 3 bps** fee, most of the depeg spread leaks to arbers during stress. LPs earn fees but lose more to inventory drift.

**Net LP economics:**

```
Net LP yield ≈ Gross fees − LVR extracted
```

If LVR dominates fees, LPs are net losers even when swap volume is high. This is the problem Oscillon targets — not by defending the peg, but by **taxing toxic drain flow** when the oracle shows peg stress.

---

## How Oscillon Addresses It

Oscillon does not rebalance, mint, or redeem. It adjusts the swap fee on **drain-direction** flow only:

| Condition | Fee |
|-----------|-----|
| Healthy / restore direction | 3 bps base |
| Drain, deviation < 3 bps | 3 bps base only |
| Drain, deviation ≥ 3 bps | 3 bps + hybrid surcharge (piecewise ∩ quadratic, max 100 bps) |

**Drain classification (USDC deployed path):**

- Oracle: Chainlink USDC/USD
- Drain when: oracle < $1 **and** USDC sold into pool (`netAmount0 > 0`)

Fee models **split the same depeg spread** — they do not create new value. The comparison metric is **LP capture %**, not total spread.

---

## Methodology

| Component | Detail |
|-----------|--------|
| Pool | Uniswap v3 USDC/USDT `0x3416cf6c708da44db2624d63ea0aaef7113527c6` |
| Swap data | Minute-level replays (demeter-fetch / BigQuery) |
| Oracle | Chainlink USDC/USD on Ethereum (`0x8fff…818f6`) |
| Calibration | Mar 2023 stress (6 days), Mar 2023 recovery (10 days), Jun 2026 calm (30 days) |
| Annual model | State-weighted over 1,277 Chainlink calendar days (BASE scenario) |
| Default backtest | Fixed historical tape (`--apply-routing` off) |

**LVR proxy (`src/lvr.py`):**

```
toxic_notional = drain_volume_usd × (depeg_bps / 10_000)
fee_revenue    = drain_volume_usd × (fee_bps / 10_000)
net_lvr        = max(0, toxic_notional − fee_revenue)

where depeg_bps = |pool_price − oracle_price| / oracle_price × 10_000
```

For USDC/USDT stablecoin pools, `oracle_price ≈ $1.00`, so this reduces to the peg deviation form.

Only **drain-direction** swaps generate LVR. Restore-direction swaps earn base fees only.

---

## Key Findings

### March 2023 SVB stress (6 days)

| Metric | Static 3 bps | Oscillon hybrid |
|--------|--------------|-----------------|
| Drain volume | $284M | $284M |
| Avg deviation | 114 bps | 114 bps |
| Max deviation | 1,167 bps | 1,167 bps |
| LP capture of spread | **14.8%** | **33.2%** |
| Large depegs (30+ bps) capture | 3.6% | 23.8% |
| Hybrid uplift | — | +$283k (6-day window; not annualizable) |

Under acute stress, static LPs retain a small fraction of the depeg spread. Oscillon hybrid roughly doubles capture — but LPs still lose the majority of spread on the largest depegs.

Chart: `output/oscillon_backtest_2023-03.png`

### Calm regime — 12 months (Jul 2025 – Jun 2026, USDC path)

| Metric | Static 3 bps | Oscillon hybrid |
|--------|--------------|-----------------|
| Drain volume | $2.67B | $2.67B |
| Avg deviation | 1.9 bps | 1.9 bps |
| LP capture | 99.0% | 100.0% |
| Incremental LP income | — | +$35k |
| Surcharge @ $500M TVL | — | **~0.70 bps/year** |

In calm micro-depeg conditions, LVR is already near zero for static LPs. Oscillon adds minimal incremental income at flagship TVL.

Chart: `output/backtest_2025-07_2026-06_usdc_oracle.png`

### Annual state-weighted surcharge (USDC, @ $500M TVL)

| Scenario | Surcharge |
|----------|-----------|
| BEAR (2024-like) | 0.02 bps/year |
| BASE (full history) | 0.72 bps/year |
| 2026-only (current regime) | 1.51 bps/year |

Oscillon is **tail/friction protection**, not a steady high-yield product at $500M+ TVL in mature USDC/USDT pools. Value concentrates in episodic stress.

Docs: `output/annual_surcharge_model.md`, `output/annual_surcharge_model_2026_only.md`

---

## Self-Correction

An early annualisation pass omitted the Mar 2023 **recovery** window (Mar 16–25) and used peak-stress economics for intermediate depeg buckets (4–15 bps). Adding `prepared_swaps_2023-03-recovery.csv` revised the 2026-only state-weighted estimate from 0.87 → 1.51 bps/year @ $500M TVL (+73%). We publish the fully calibrated number, not partial-window extrapolations.

Where backtest variants disagree (e.g. additive vs hybrid fee curve), the model is labeled explicitly. Production default is **Oscillon hybrid**.

---

## What Oscillon Is Not

- **Not a peg defense mechanism** — does not mint, redeem, or hold reserves.
- **Not a universal LP yield enhancer** — calm-regime surcharge at $500M TVL is low single-digit bps/year.
- **Not proven without caveats** — default backtest uses fixed historical tape; volume may route away when fees rise (`--apply-routing` models this).
- **Not wallet-attributed** — minute data has no sender addresses; cannot identify who pays the surcharge.

---

## Limitations

| Limitation | Effect |
|------------|--------|
| Flow-based LVR proxy | Approximates adverse selection; not full AMM mark-to-market |
| Fixed tape (default) | Same swap volume for static and hybrid; may overstate calm-regime uplift |
| Oracle `merge_asof` backward | Can understate fast depegs — conservative for Oscillon |
| Integer hook fees in backtest | Slightly understates LP income vs float formula |
| Minute aggregates | No per-wallet bot vs retail split |
| USDT leg | Counterfactual only — different oracle cadence; not deployed |

---

## Reproduce

```bash
# Mar 2023 stress
python3 scripts/backtest_mainnet.py --prepared data/prepared_swaps_2023-03.csv

# 12-month USDC deployed path
python3 scripts/backtest_mainnet.py --prepared data/prepared_swaps_2025-07_2026-06_usdc_oracle.csv

# Annual surcharge model
python3 scripts/build_annual_surcharge_model.py
python3 scripts/build_annual_surcharge_model.py --scenario 2026-only

# Tests
python3 -m pytest tests/ -q
```

---

## Key Files

| File | Purpose |
|------|---------|
| `src/lvr.py` | Core LVR proxy for stablecoin drain swaps |
| `src/oscillon_fee.py` | Hybrid fee curve (production default) |
| `src/swap_direction.py` | Drain classification by oracle leg |
| `src/backtest_engine.py` | Swap-level backtest economics |
| `scripts/backtest_mainnet.py` | Main backtest + charts |
| `scripts/prepare_data.py` | Merge oracle + minute swaps |
| `scripts/build_annual_surcharge_model.py` | State-weighted APR model |
| `output/backtest_2025-07_2026-06_usdc_oracle.png` | 12-month calm regime |
| `output/oscillon_backtest_2023-03.png` | Mar 2023 stress proof |

---

## Summary

Stablecoin LPs lose most of the depeg spread to LVR during stress (14.8% capture at 3 bps static in Mar 2023). Oscillon's hybrid dynamic fee raises that to 33.2% on the same tape — meaningful protection, not elimination. In calm regimes, LVR is already small and incremental surcharge at $500M TVL is ~0.7–1.5 bps/year. The product is best understood as **LP insurance against toxic drain flow during peg dislocations**, with economics that scale inversely with TVL and spike episodically under stress.
