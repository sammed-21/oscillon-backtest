# Oscillon LVR Research — Research Summary

_A self-contained writeup of the research methodology, findings, and engineering
rigor behind this project, prepared for technical review._

---

## 1. What this research answers

Stablecoin AMM pools leak value to arbitrageurs during depegs — a specialized
case of Loss-Versus-Rebalancing (LVR). This project backtests a dynamic
depeg-surcharge fee mechanism against real on-chain swap and oracle data to
answer three questions a quant researcher is actually paid to answer, not just
"does the mechanism work":

1. **How much value does it actually recover, and under what conditions?**
2. **Does the result generalize, or is it an artifact of the specific window tested?**
3. **What are the honest limits of the methodology, and where would it be wrong to extrapolate?**

---

## 2. Methodology

### 2.1 Data

- Real on-chain swap history (Uniswap v3 and v4), fetched directly from raw
  event logs, not a single vendor's pre-decoded convenience table.
- Real Chainlink oracle price history, resolved through Chainlink's on-chain
  **Feed Registry** contract when a feed wasn't discoverable through the
  public listing — i.e., verifying oracle existence at the protocol level
  rather than trusting a website's index.
- Seven independent asset pairs spanning three distinct peg-maintenance
  mechanisms (fiat/crypto-collateralized, delta-neutral synthetic, legacy
  fractional-algorithmic) and two chains, to separate "mechanism-driven
  finding" from "one lucky pool."

### 2.2 Data integrity — a four-stage verification pass, applied to every dataset

Every dataset that went into a headline number passed the same four checks
before being trusted, not after a result looked interesting:

| Check                       | What it catches                                                                                                                         |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Completeness**            | Fetched row count vs. an independently re-queried ground truth count                                                                    |
| **Aggregation correctness** | Minute-level bars re-derived from raw swap-level data, checked for exact match — not "close enough"                                     |
| **Sanity bounds**           | Every implied price checked against a plausible range across the _entire_ dataset, not spot-checked                                     |
| **Random on-chain sample**  | N rows per dataset independently re-derived from raw RPC transaction receipts, bypassing every indexing layer used to build the dataset |

Every dataset that shipped a headline number passed with a 100% match rate on
the RPC cross-check — the one verification step that cannot be fooled by an
upstream indexing bug, because it re-derives the answer from the chain
directly rather than trusting any intermediary.

### 2.3 Statistical validity — out-of-sample testing, not a single fitted window

The dynamic fee mechanism has no fitted parameters — every constant (curve
steepness, activation threshold, fee ceiling) is a fixed design choice, not
something calibrated on the data being tested. To confirm the headline result
wasn't an artifact of the specific window studied, the largest single-asset
dataset was split chronologically into a training period and a strictly
later, disjoint test period, with **zero parameter changes** between the two:

| Period                        | Static-fee LP capture | Dynamic-fee LP capture | Improvement |
| ----------------------------- | --------------------- | ---------------------- | ----------- |
| Training window               | 50.6%                 | 63.2%                  | +12.6 pp    |
| Test window (later, disjoint) | 62.4%                 | 73.6%                  | +11.2 pp    |

The improvement held out-of-sample within 1.4 percentage points despite the
two windows representing genuinely different market regimes (the test window
was calmer overall — both static and dynamic capture rose together). The
relevant read is the _stability of the gap_, not the absolute level, and the
gap did not degrade.

---

## 3. Key findings

### 3.1 The mechanism's value is regime- and mechanism-dependent, not universal

Backtesting the same fee design across seven real pools produced a
100x range in average deviation and a correspondingly wide range in how much
value a static fee already leaves on the table:

| Peg mechanism (asset class)                                | Avg. deviation | Static-fee LP capture |
| ---------------------------------------------------------- | -------------- | --------------------- |
| Fiat/crypto-collateralized (multiple pairs)                | ~1–3 bps       | 97–100%               |
| Delta-neutral synthetic dollar                             | ~2–8 bps       | 50–99%                |
| Legacy fractional-algorithmic, no longer actively defended | ~77 bps        | 7%                    |

![Cross-asset deviation and static-fee capture across 7 real pools](output/cross_asset_summary.png)

**Conclusion, stated the way a research desk would want it stated:** a
uniform dynamic-fee design is close to redundant on mature, tightly-pegged
collateralized stablecoins — there's simply not enough deviation for it to
recover — and is most valuable precisely where a stablecoin's peg-holding
mechanism itself introduces structural latency. This is a testable,
falsifiable claim, not a marketing one, and it directly reframes where the
mechanism should be deployed.

### 3.2 Regime sensitivity is large and must be reported as a range, not a point estimate

The same mechanism, same asset, same fee curve:

| Regime                                               | Static capture | Dynamic-fee capture |
| ---------------------------------------------------- | -------------- | ------------------- |
| Acute stress event (single historical depeg episode) | 14.8%          | 33–40%              |
| Calm baseline period                                 | 97.8%          | 99.9%               |

![March 2023 acute stress backtest: fee curves, LVR distribution, cumulative LP income, and capture by market condition](output/oscillon_backtest_2023-03.png)

A state-weighted annual model (blending historical regime frequencies rather
than naively extrapolating either extreme) puts the realistic long-run
incremental yield in the low single-digit basis points per year at scale —
explicitly _not_ the acute-stress number, which is real but not
representative of steady state. Reporting only the best-case number here
would have been a materially misleading research output; the range is the
finding.

### 3.3 A real engineering incident worth surfacing on its own

While building the oracle pipeline for one asset, a Chainlink price feed
appeared to have stopped updating for five consecutive months — which, if
trusted, would have silently zeroed out a genuine chunk of real depeg
history in the backtest. Direct on-chain verification (calling the feed's
`latestRoundData()` function directly, bypassing every indexing layer) showed
the feed was live the entire time; the actual fault was in a third-party
indexer's pre-decoded table for that specific contract. The fix required
tracing the feed's proxy contract to its underlying aggregator and reading
raw event logs directly rather than trusting any decoded convenience table.
This is the class of bug that silently corrupts a backtest's conclusions
without ever throwing an error — catching it required treating "the data
looks plausible" as insufficient evidence that it's correct.

Other data-integrity issues found and fixed the same way, by design rather
than by luck:

- A floating-point precision bug in swap aggregation that silently
  rounded large token amounts, affecting the majority of high-volume minutes
  before being caught and fixed with exact-integer arithmetic.
- A hardcoded assumption in the backtest's own reporting layer that mislabeled
  which oracle was actually driving a given result once the pipeline was
  extended beyond the original single asset it was written for.

---

## 4. Explicit limitations — what this research does not claim

A researcher who states no limitations either hasn't looked hard enough or
isn't telling you something. Stated plainly:

- **Fixed-volume counterfactual.** Headline figures hold trading volume
  constant across fee models — they answer "what if this fee had applied to
  the same historical trades," not "what volume would route elsewhere at a
  higher fee." An elastic-routing variant exists and shows real income is
  somewhat lower once fee-sensitivity is modeled.
- **A backtest quantifies payout, not the risk premium a rational allocator
  should demand** for taking on a novel, oracle-dependent smart contract to
  earn it. That's a separate, unresolved question this data cannot settle on
  its own.
- **Point-in-time data snapshots** drift slightly from a live re-query as the
  chain keeps moving — expected, and quantified (sub-1% in every case
  checked), not a silent error.
- **A TVL assumption used for cross-asset comparability is a standardized
  benchmark, not each pool's real size** — real-pool percentage yields differ
  substantially from the benchmarked figures, and both are reported
  separately rather than conflated.

---

## 5. What this demonstrates, directly

- **Research design**: falsifiable hypotheses, out-of-sample validation,
  explicit regime decomposition instead of a single point estimate.
- **Data engineering**: building fetch/verification pipelines from raw
  on-chain primitives across multiple chains and AMM architectures, not just
  consuming an API.
- **Skepticism as a working method**: every dataset was checked for ways it
  could be wrong before being used, and real bugs were found this way — not
  hypothetically, but concretely, with the incident and fix documented above.
- **Communicating uncertainty honestly**: ranges and regime-dependence
  reported instead of a single flattering number, limitations stated before
  being asked.

---

_Full methodology, verification logs, and reproducible commands are available
in the accompanying repository (`BACKTEST.md`, `BACKTEST_AUDIT.md`, and the
`scripts/` directory)._
