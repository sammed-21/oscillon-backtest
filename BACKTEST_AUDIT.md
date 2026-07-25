# Oscillon Backtest Methodology Audit

Audit of `scripts/backtest_mainnet.py` and supporting modules against professional
quantitative research standards. All line references verified against the current
repo state on branch `saffron/valut-lvr-backtest`; conservation identity and LP
capture sensitivity figures below were computed live, not read off prior reports.

---

## Q1 — Data source and representativeness

**Raw source:** real on-chain Uniswap v3 USDC/USDT pool
(`0x3416cf6c708da44db2624d63ea0aaef7113527c6`, Ethereum), fetched two ways:
- Minute-aggregated files (`data/*.minute.csv`) via a BigQuery-backed fetch tool
  (`data/demeter_fetch_uniswap_bigquery.toml`) or an RPC-backed path
  (`data/demeter_fetch_uniswap_rpc.toml`, Infura + Etherscan).
- A raw per-swap path (`scripts/prepare_data.py:109`, the `else` branch) that reads
  `sqrt_price_x96` and `block_timestamp` directly per swap.

**Row granularity — important distinction:** the primary data path used for the
March 2023 and June 2026 datasets is the **minute-aggregated** file
(`prepare_data.py:83-113`, `--use-minute-files`). Each row is **one minute of pool
activity**, not one swap transaction — `netAmount0`/`netAmount1` are net flows over
that minute, `closeTick` is the tick at minute close. The alternate path
(`prepare_data.py:109-113`) does operate on real per-swap `sqrt_price_x96` rows when
used, but the datasets actually shipped in this repo (`prepared_swaps_2023-03.csv`,
`prepared_swaps_2026-06.csv`, etc.) are minute-level.
**This is a limitation, not standard practice for a "swap-level" backtest** — intra-minute
swaps that partially offset (a drain swap followed by a restore swap in the same
minute) are netted before drain classification, which can understate both drain
volume and LVR within volatile minutes. It should be labeled "minute-level" not
"swap-level" in any external write-up.

**Columns and meaning:** `timestamp`, `netAmount0`/`netAmount1` (net token flow,
minute), `closeTick`/`openTick`/`lowestTick`/`highestTick` (tick range within the
minute), `pool_price` (derived from tick or sqrtPriceX96), `oracle_price` (Chainlink,
merged via `merge_asof`), `dev_bps` (deviation), `is_drain`, `drain_size_usd`,
`restore_size_usd`, `swap_size_usd`.

**Filtering:** `prepare_data.py:207` drops rows with `swap_size_usd <= $100`
(`--min-swap-usd`, default 100). This is disclosed in the printed methodology block
in `backtest_mainnet.py:436` ("Rows with swap_size_usd < $100 dropped at prepare
time"). Standard practice — dust filtering is normal — but it does mean the backtest
is not a complete replay of 100% of on-chain activity.

---

## Q2 — Simulation methodology

Per-row flow, traced through `src/backtest_engine.py:simulate_swap_row` (lines 41-112):

1. **Inputs read from row** (lines 57-61): `dev_bps`, `is_drain`, `drain_size_usd`
   (falls back to `swap_size_usd`), `restore_size_usd`.
2. **Fee applied** (line 64 or 88): calls the model's `fee_fn(dev_bps, is_drain)` —
   e.g. `static_fee` always returns 3 bps; `oscillon_fee_hybrid` returns the hybrid
   curve value.
3. **Routing/volume retention** (lines 65-71): computes what fraction of drain volume
   "stays" — default 1.0 (see Q3).
4. **LP income** (lines 82-84): `eff_drain * fee_drain/10000 + restore_size * BASE_FEE_BPS/10000`.
5. **LVR** (line 86): `max(0, eff_drain * (dev_bps - fee_drain) / 10000)`.
6. **Output row**: dict with `lp_income`, `lvr`, `spread_captured`, `volume_lost`, etc.
7. **Accumulation**: `backtest_mainnet.py:440-455` runs this per-row for every model,
   collects into a DataFrame; `summarize_backtest` (`backtest_engine.py:145-172`)
   sums `lp_income`/`lvr` across all rows and derives `lp_capture_pct`, `stress_apr`.

**Is this a trade replay?** Yes, in the qualified sense above — it replays the
historical sequence of minute-level net flows and applies each fee model's fee to
that same flow, i.e. a **counterfactual fee substitution on a fixed historical tape**,
not a full order-book/AMM re-simulation. It does not re-derive what price impact or
routing would have looked like under a different fee (unless `--apply-routing` is
set — see Q3). This is a standard first-pass backtest design for fee-mechanism
research; it is not a full agent-based market simulation.

---

## Q3 — The counterfactual assumption

**Default: Option A (fixed counterfactual)** — confirmed in code.

```python
# src/backtest_engine.py:34-38
def drain_routing_retention(..., apply_routing: bool = False) -> float:
    if not apply_routing:
        return 1.0   # full historical drain volume retained regardless of fee
```

```python
# backtest_mainnet.py:365-369 (CLI)
p.add_argument("--apply-routing", action="store_true",
    help="Scale drain volume by elastic routing model when fee > competitor (default: off)")
```

By default (`apply_routing=False`), every model — static, hybrid, piecewise, etc. —
sees the **identical drain volume**; only the fee applied to it differs. `Option B`
exists (`retained_volume_fraction` in `src/volume_model.py`, wired through
`drain_routing_retention`) but is opt-in via `--apply-routing`.

**This is the standard first-order approximation** for a fee-mechanism backtest, and
the code is honest about it — `backtest_mainnet.py:434` prints "Volume Lost:
REPORTED ONLY — does not change LP/LVR unless --apply-routing" in the methodology
banner every run. **Flag as a limitation:** default results overstate Oscillon's
absolute LP income to the extent that higher drain-fee routing would have driven some
volume elsewhere. The elastic-routing variant exists and should be run alongside the
fixed-counterfactual headline for any external-facing claim.

---

## Q4 — LVR formula validation

Implemented in two places (consistent formulas, `src/lvr.py` is the standalone/simpler
version, `src/backtest_engine.py:86` is what the CLI actually uses):

```python
# src/backtest_engine.py:86
lvr = max(0.0, eff_drain * (dev - fee_drain) / 10_000) if dev > 0 else 0.0
```

This matches the target spec `net_lvr = max(0, drain_volume × (dev_bps − fee_bps) / 10000)`,
applied only when `drain=True` (line 63 gate). Note: the noise-floor gate
(`dev_bps <= 5` → zero LVR) lives in the standalone `src/lvr.py:31`
(`minute_lvr`), **not** in `backtest_engine.simulate_swap_row` — the CLI path used by
`backtest_mainnet.py` has no explicit small-depeg floor beyond `dev > 0`. This is a
minor inconsistency between the two LVR implementations worth reconciling if both are
cited externally.

**Conservation check — run live on the first 10 drain swaps of `prepared_swaps_2023-03.csv`
under the static (3 bps) model:**

| dev_bps | drain ($) | lp_income ($) | lvr ($) | expected spread = drain×dev/10000 ($) | diff |
|---|---|---|---|---|---|
| 24.41 | 6,979.87 | 2.0940 | 14.9427 | 17.0367 | 0.0000% |
| 24.41 | 772.58 | 0.2318 | 1.6540 | 1.8857 | 0.0000% |
| 24.41 | 10,427.41 | 3.1282 | 22.3233 | 25.4515 | 0.0000% |
| 24.41 | 126,688.11 | 38.0064 | 271.2177 | 309.2241 | 0.0000% |
| 24.41 | 60,299.19 | 18.0898 | 129.0903 | 147.1801 | 0.0000% |
| 24.41 | 32,954.64 | 9.8864 | 70.5503 | 80.4367 | 0.0000% |
| 24.41 | 13,089.80 | 3.9269 | 28.0230 | 31.9500 | 0.0000% |
| 24.41 | 106,284.37 | 31.8853 | 227.5368 | 259.4221 | 0.0000% |
| 24.41 | 704.81 | 0.2114 | 1.5089 | 1.7203 | 0.0000% |
| 24.41 | 25,922.00 | 7.7766 | 55.4946 | 63.2712 | 0.0000% |

Identity holds exactly (floating-point precision only) on all 10 rows checked.

---

## Q5 — What "LP income" measures

```python
# src/backtest_engine.py:82-84
lp_income = (
    eff_drain * fee_drain / 10_000 + restore_size * BASE_FEE_BPS / 10_000
)
```

Answer: **A) total fees earned by all LPs in the pool** for that minute's flow —
computed as fee-rate × volume, not scaled to any single LP's share. It is **not** (C)
just the surcharge above base fee — the full fee (base + surcharge on drain,
base-only on restore) is included. Since fee income here is a rate applied to
observed volume, it does **not** depend on TVL — TVL only enters later, in
annualization (Q6). This matters: **"LP income" in dollars is TVL-independent; only
the derived APR is TVL-dependent.**

---

## Q6 — TVL assumption

TVL is used **only** in `summarize_backtest` (`backtest_engine.py:158-159`):

```python
stress_apr = (total_lp_income / tvl) * (365 / period_days) * 100 if tvl > 0 else 0.0
```

Default `--tvl 15_000_000` (`backtest_mainnet.py:350`), a **fixed exogenous input**,
not derived from the historical data (the historical `currentLiquidity` column exists
in the raw data but is not used for the APR denominator).

**Sensitivity:** `stress_apr` is linear in `1/tvl`. At 2× TVL ($30M), the reported
Stress APR halves; at 0.5× TVL ($7.5M), it doubles. The absolute dollar LP income and
LVR numbers are unaffected by the TVL assumption — only the annualized percentage is.
This is disclosed via the `--tvl` CLI flag but the *default* $15M figure should be
explicitly justified (e.g., against the pool's actual historical TVL) in any external
report, since it is the single biggest lever on the headline APR number.

---

## Q7 — Time period and regime bias

| File | Date range | Swaps | Mean dev (bps) | Max dev (bps) | Regime |
|---|---|---|---|---|---|
| `prepared_swaps_2023-03.csv` | 2023-03-10 → 03-15 | 6,232 | 114.16 | 1,167.0 | SVB stress |
| `prepared_swaps_2026-06.csv` | 2026-06-01 → 06-30 | 17,239 | 3.23 | 4.32 | Calm |
| `prepared_swaps_2026_h1_usdc_oracle.csv` | 2026-01-01 → 06-30 | 95,399 | 2.18 | 5.30 | Calm, full H1 |
| `prepared_swaps_2025-07_2026-06_usdc_oracle.csv` | 2025-07-01 → 2026-06-30 | 218,416 | 1.87 | 28.72 | 12-month blended |

**LP capture % sensitivity — computed live, static vs. hybrid, same code path:**

| Regime | Static LP capture % | Hybrid LP capture % |
|---|---|---|
| Stress (Mar 2023, 6 days) | **14.8%** (LP $214,645 / LVR $1,237,700) | **34.3%** (LP $497,602 / LVR $954,743) |
| Calm (Jun 2026, 30 days) | **93.6%** (LP $88,230 / LVR $6,060) | **100.0%** (LP $99,943 / LVR $23) |

The headline result is highly regime-sensitive — a ~6.3x swing in static LP capture
% (14.8% → 93.6%) depending purely on which window is chosen. **Any single-number
headline claim must state the window it came from.** `BACKTEST.md` does this
correctly today (separate stress/calm/annual sections); any condensed summary for
external audiences must preserve that structure rather than quoting one number.

---

## Q8 — What this backtest cannot measure

| # | Question | Fatal flaw or acceptable limitation? |
|---|---|---|
| 1 | Would traders route away at higher fees? | **Acceptable limitation, partially addressed.** `--apply-routing` implements an elastic-demand counterfactual (Q3); off by default. Not fatal because the model is transparent about it, but any claim without running the routing variant is optimistic. |
| 2 | Would higher LP returns attract more TVL, changing APR? | **Acceptable limitation.** TVL is exogenous and fixed (Q6); no feedback loop from yield → deposits → TVL → yield is modeled. Standard for a first-pass fee-mechanism study; would need a market-equilibrium model to fix, which is out of scope for a backtest. |
| 3 | Same mechanism performance on other chains (block time, MEV)? | **Acceptable limitation, out of scope by design.** This is an Ethereum L1 mainnet historical replay; nothing here models a different chain's block time or MEV searcher behavior. Must be flagged explicitly if Oscillon is proposed for deployment elsewhere (e.g. the Robinhood-chain / 24h vault discussion). |
| 4 | Oracle unavailable / black swan? | **Acceptable limitation, but worth hardening.** No modeled fallback-to-fallback failure; `merge_asof(direction="backward")` with no max gap (`prepare_data.py:181`) means a stale oracle read is used indefinitely rather than triggering a "no data" state — conservative for LVR (understates depeg) but not a simulation of oracle outage risk. |
| 5 | Does 5 bps noise floor miss real LVR below threshold? | **Acceptable limitation, and inconsistently applied (see Q4).** `src/lvr.py` has an explicit 5 bps floor; `backtest_engine.simulate_swap_row` (used by the CLI) does not — it only floors at `dev > 0`. Reconcile before quoting "5 bps noise floor" as if it universally applies; in the actual CLI path, LVR below 5 bps is small in magnitude but not zeroed. |

None of these are fatal to using the backtest as *directional* evidence for the
mechanism (fee models split depeg spread, hybrid captures more than static). They
would be fatal to a claim like "Oscillon will generate $X of *additional pool TVL* or
*guaranteed volume-adjusted* revenue" — that class of claim needs #1 and #2 addressed.

---

## Report

### Section 1 — What this backtest measures (plain English)

This backtest replays historical minute-level Uniswap USDC/USDT pool activity and
asks: "if a different fee schedule had been in effect during this same period, how
would the split between LP income and value lost to arbitrageurs (LVR) have changed?"
It holds trading volume fixed across every fee model being compared — static 3 bps,
Oscillon's hybrid dynamic surcharge, and several reference variants — and only swaps
in which fee is applied. It is not a simulation of trader behavior, pool TVL growth,
or cross-chain deployment; it is a controlled counterfactual over one fixed historical
tape, run separately over a 6-day acute stress window (March 2023 SVB depeg) and
multi-month calm windows (2025-2026).

The core economic idea it tests: during a depeg, arbitrageurs profit from the gap
between the stale pool price and the true market price; a fee high enough to close
that gap captures more of the spread for LPs instead of arbitrageurs. The backtest
quantifies exactly how much of that spread each fee model recovers, under the
assumption that the same trades would have happened regardless of the fee charged.

### Section 2 — Methodology (research-paper methods section)

Data: minute-aggregated Uniswap v3 USDC/USDT swap flow (Ethereum mainnet, pool
`0x3416cf6c708d...527c6`), sourced via BigQuery/RPC fetch, merged with Chainlink
USDC/USD oracle prices via backward as-of join (no staleness cap). Dust rows below
$100 notional are excluded. Depeg deviation is computed as `|oracle_price − 1.00| ×
10,000` in basis points; drain-direction flow is classified per swap-minute as
token0 (USDC) sold into the pool while the oracle reads below $1.00.

For each fee model f and each row i, LP income and LVR are computed as:

```
lp_income_i = drain_i × f(dev_i)/10000 + restore_i × base_fee/10000
lvr_i       = max(0, drain_i × (dev_i − f(dev_i))/10000)     [drain rows only]
```

Aggregate LP capture % = Σlp_income / Σ(lp_income + lvr) over a window. Annualized
"Stress APR" = (Σlp_income / TVL) × (365/period_days) × 100, with TVL as an exogenous
fixed input (default $15M). Volume is held fixed across models by default (fixed
counterfactual); an elastic-routing variant (`--apply-routing`) is available but not
used for headline figures.

### Section 3 — Validated findings

- The core conservation identity (`lp_income + lvr = drain_volume × dev_bps/10000`)
  holds exactly across all spot-checked rows — the accounting is internally
  consistent, not just asserted.
- Under the fixed-volume counterfactual, Oscillon's hybrid fee materially increases
  LP capture % relative to static 3 bps in both regimes tested: 14.8% → 34.3% in the
  March 2023 stress window, and 93.6% → 100.0% in the June 2026 calm window.
- The magnitude of this uplift is strongly regime-dependent — the mechanism's value
  concentrates in stress periods with large deviations, and is marginal in calm
  regimes where LVR is already small under static fees. This is consistent and
  reproducible across the datasets checked.
- Dollar-denominated LP income and LVR figures do not depend on the TVL assumption;
  only the annualized APR derived from them does.

### Section 4 — Explicit limitations

- Data is minute-aggregated, not true per-swap — intra-minute netting can understate
  both drain volume and LVR in fast-moving minutes.
- Default results assume trade volume is fee-inelastic (Option A); the code has an
  elastic-routing alternative but it is not what's reported by default. Headline
  numbers therefore likely overstate LP income to the extent traders would route
  elsewhere under higher drain fees.
- No feedback from LP yield to deposited TVL is modeled — TVL is a fixed external
  assumption, and the headline "Stress APR" is linear in 1/TVL, making the choice of
  TVL assumption a major, under-scrutinized lever on the headline number.
- Results are computed for Ethereum L1 mainnet only; no chain-specific block time,
  gas cost, or MEV searcher behavior is modeled, which matters directly for the
  Robinhood-chain / 24-hour-vault deployment being considered separately.
- The 5 bps LVR noise floor exists in one module (`src/lvr.py`) but not the one
  actually driving the CLI backtest (`src/backtest_engine.py`) — this inconsistency
  should be resolved before quoting a noise floor as a blanket property of the
  methodology.
- No modeling of oracle outage / black-swan unavailability; a stale oracle read is
  used indefinitely rather than triggering an explicit "no data" state.

### Section 5 — What would be needed to address each limitation

1. **Minute-aggregation:** re-run the pipeline against true per-swap data (the
   `sqrt_price_x96`/`block_timestamp` path already exists in `prepare_data.py:109`,
   just not what generated the shipped datasets) to check whether intra-minute
   netting materially changes LVR totals.
2. **Fixed-volume assumption:** run `--apply-routing` alongside every headline
   comparison and report both bounds (fixed vs. elastic) rather than only the
   optimistic fixed case.
3. **TVL feedback:** out of scope for a backtest; would require either (a) sourcing
   actual historical pool TVL from the minute data's `currentLiquidity` column
   instead of a fixed assumption, or (b) a separate equilibrium/deposit-flow model —
   flag as a modeling extension, not a backtest fix.
4. **Cross-chain applicability:** would need equivalent minute-level swap + oracle
   data from the target chain (e.g. Robinhood chain) once available; nothing in the
   current pipeline is Ethereum-specific by design, so it's a data-acquisition gap,
   not a methodology rewrite.
5. **Noise-floor reconciliation:** a one-line fix — decide whether `simulate_swap_row`
   should floor at the same 5 bps threshold as `src/lvr.py`, and make both paths
   consistent.
6. **Oracle outage modeling:** add an explicit staleness cap to the `merge_asof` join
   (e.g. drop or flag rows where oracle data is older than N minutes) so "oracle
   unavailable" is a distinguishable state rather than silently reusing the last
   known price.
