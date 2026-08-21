# USDe/USDT and USDe/USDC v4 Swap Data — Provenance

How this data was obtained, verified, and what its limitations are. Written so it
can be shared alongside the data itself.

---

## Pools

| Pool | Type | Identifier | Chain |
|------|------|------------|-------|
| USDe/USDT | Uniswap v4 | PoolId `0x63bb22f47c7ede6578a25c873e77eb782ec8e4c19778e36ce64d37877b5bd1e7` | Ethereum |
| USDe/USDC | Uniswap v4 | PoolId `0x56fc29b86900aa0afa6e20b020429bffba1105cfb45070492c67529b46eb48c1` | Ethereum |

Both identifiers are **32-byte Uniswap v4 PoolIds**, not contract addresses — v4
has no per-pool contract. Every pool's swaps are emitted from a single shared
`PoolManager` singleton contract, and individual pools are distinguished only by
this `id` field in the event log. This is why the standard v3-style fetch tooling
already in this repo (`demeter-fetch`, keyed on a pool's own contract address)
could not be used as-is — it queries a specific pool contract's Swap events
directly, which doesn't exist for v4.

Token order/decimals used: token0 = USDe (18 decimals), token1 = USDT or USDC
(6 decimals each) — confirmed correct, not just assumed (see Verification below).

---

## Source

**Dune Analytics**, decoded table `uniswap_v4_ethereum.PoolManager_evt_Swap`.
This is Dune's spellbook-decoded version of the raw `Swap` event log emitted by
Uniswap v4's `PoolManager` contract on every swap, across every v4 pool on
Ethereum. Columns used: `evt_block_time`, `amount0`, `amount1`, `tick`,
`liquidity`, filtered by `id = <PoolId>`.

Queried via Dune's SQL execution API (`api.dune.com/api/v1/sql/execute` →
poll `.../status` → `.../results`), using the `DUNE_API_KEY` already configured
in this repo's `.env`. Same three-call pattern already used for Chainlink oracle
fetching in `scripts/fetch_data.py::fetch_dune_chainlink` (the code you have
selected) — the new fetch script reuses its session/retry and date-chunking
helpers rather than duplicating them.

**Fetch script:** `scripts/fetch_dune_v4_swaps.py` (new, written for this task).
Pulls raw swap-level rows chunked by date (7-day chunks), then aggregates them
into minute bars matching the exact column schema of the existing v3 minute
files (`timestamp,netAmount0,netAmount1,closeTick,openTick,lowestTick,
highestTick,inAmount0,inAmount1,currentLiquidity`), so the rest of the pipeline
(`prepare_data.py --use-minute-files`) works unmodified.

---

## What was tried first and ruled out

1. **BigQuery** (`data/demeter_fetch_uniswap_bigquery.toml`) — the standard v3
   fetch path used for the USDC/USDT pool throughout this project. Failed with
   `403 Forbidden: Quota exceeded — free query bytes scanned` (see
   `data/fetch_usde_swaps.log`) before ever reaching swap data for USDe. This is
   a GCP billing/quota limit, unrelated to v4.
2. **Infura RPC** (`demeter-fetch` RPC mode) — attempted against two candidate
   v3-style addresses. Both failed:
   - The USDe/USDT address initially assumed (`0x435664008f38b0650fbc1c9fc971d0a3bc2f1e47`,
     labeled "legacy v3 pool") and a supposed USDe/USDC v3 address
     (`0xE6D7EbB9f1a9519dc06D557e03C522d53520e76A`) were both checked. The
     USDC one turned out not to correspond to any real pool (confirmed via
     Uniswap's own UI search — the only USDe/USDC pools that exist have
     ~$63 TVL or less, except the real v4 pool at ~$612k TVL).
   - Two parallel RPC pilot fetches also hit Infura's free-tier rate limit
     (`429 Too Many Requests`) before resolving either way.
3. **Conclusion:** the only pools with meaningful liquidity for both pairs are
   v4. No usable v3 fallback exists for either pair.

---

## Verification performed (not just assumed correct)

Before trusting this data, three checks were run:

1. **Table/schema exists and is queryable** — a `LIMIT 1` query against
   `uniswap_v4_ethereum.PoolManager_evt_Swap` returned a real row with the
   expected columns (`amount0, amount1, fee, id, liquidity, sender,
   sqrtPriceX96, tick`, etc.) before any pool-specific query was built.
2. **Real swaps exist for both specific PoolIds** — confirmed via
   `MIN(evt_block_time)`/`MAX(evt_block_time)`/`COUNT(*)` queries per pool:
   - USDe/USDT: first swap **2025-10-02**, latest **2026-08-20**, 118,395 total swaps.
   - USDe/USDC: first swap **2026-07-17** (pool did not exist before this —
     the initial July 1–3 pilot window returned 0 rows for exactly this reason,
     not a fetch error), latest **2026-08-20**, 17,585 total swaps as of fetch time.
3. **Token order / decimals sanity check** — converted a sample `closeTick`
   from each pool to a price using `src/uniswap_math.py::tick_to_price` with
   the assumed decimals (USDe=18, USDT/USDC=6). Both came back within a few bps
   of $1.00 (USDe/USDT: 1.000403, USDe/USDC: 0.999903), confirming the
   token0/token1 assignment in `src/pool_config.py` is correct — an inverted
   order or wrong decimals would have produced a wildly wrong magnitude
   (e.g. ~1e12 or ~1e-12), not a number near 1.0.

---

## What was actually fetched

| Pool | Date range fetched | Reason for range | Raw swap rows | Daily minute files | Saved to |
|------|--------------------|--------------------|---------------|---------------------|----------|
| USDe/USDT | 2025-10-02 → 2026-08-20 | Full available history — pool has no data before 2025-10-02 | 118,395 | 323 | `data/usde_usdt/` |
| USDe/USDC | 2026-07-17 → 2026-08-20 | Full available history — pool didn't exist before 2026-07-17 | 17,585 | 35 | `data/usde_usdc/` |

Raw (pre-aggregation) swap-level CSVs are also kept alongside the minute files
as `v4_swaps_raw_<start>_<end>.csv` in each folder, in case a different
aggregation window is ever needed.

Neither range is the "last one year" originally requested — both are capped by
when each pool actually started trading, not a fetch limitation. USDe/USDT
covers ~10.5 months; USDe/USDC covers ~5 weeks.

---

## USDe oracle data (separate source, already existed)

Chainlink USDe/USD price feed, contract `0xa569d910839Ae8865Da8F8e70FfFb0cBA869F961`,
fetched via the same Dune `chainlink_ethereum.EACAggregatorProxy_v2_call_latestAnswer`
table used for USDC/USDT — see `scripts/fetch_data.py::fetch_dune_chainlink`.
Saved at `data/chainlink_usde_2025-07_2026-06.csv`, only 71 rows across the
full window — this is expected, not a fetch failure: the feed only emits a
new row when price moves enough to trigger an update, and USDe has stayed
extremely close to $1.00 for nearly this entire period.

---

## Known gaps / not yet done

- **Not yet run through `prepare_data.py`** — the minute files exist but have
  not yet been merged with the Chainlink oracle or classified into
  drain/restore swaps. That's the next step before any LVR backtest can run
  on this data.
- **Stray leftover files** in `data/usde_usdt/` and `data/usde_usdc/` —
  `fetch_rpc.toml` and `ethereum_height_timestamp.sqlite` in each folder are
  dead artifacts from the abandoned RPC attempts (item 2 above), not part of
  the working pipeline. Safe to delete.
- **No independent second-source cross-check** — unlike the USDC/USDT pool
  (which has both BigQuery and RPC paths historically exercised), this data
  has only been pulled from Dune once. No second data source has verified the
  same swaps independently.
- **v4 pool_price convention assumed consistent with v3** — `netAmount0 > 0`
  is assumed to mean "token0 sold into the pool," matching the v3 convention
  used everywhere else in this repo (`src/swap_direction.py`). The Q1 sanity
  check (sign of `amount0`/`amount1` in the sample row) was consistent with
  this, but it has not been independently verified against a second source
  (e.g. a block explorer transaction) the way the v3 conservation identity was
  checked earlier in this project.
