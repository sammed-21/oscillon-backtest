#!/usr/bin/env node
/**
 * Fetch Uniswap v3 Swap logs and write demeter-compatible *.minute.csv
 * Uses ethers v6 only (no Python web3 / demeter-fetch).
 *
 *   cd fetch-ethers && npm install
 *   cp .env.example .env   # optional: set ETHEREUM_RPC_URL
 *   npm run fetch -- --chain ethereum --hours 6
 *
 * Output: ../data/{chain}-{pool}-YYYY-MM-DD.minute.csv
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Interface, JsonRpcProvider, Network, getAddress } from "ethers";
import { CHAINS, DEFAULT_CHAIN, PUBLIC_RPC } from "./chains.mjs";

const __dir = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dir, "..");
const DATA_DIR = join(REPO_ROOT, "data");

const SWAP_IFACE = new Interface([
  "event Swap(address indexed sender, address indexed recipient, int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)",
]);
const SWAP_TOPIC = SWAP_IFACE.getEvent("Swap").topicHash;

const BLOCK_TIME_SEC = { ethereum: 12 };
const CHUNK_SIZE = 9_000;

async function loadEnvFileAsync() {
  const path = join(__dir, ".env");
  if (!existsSync(path)) return;
  const { readFile } = await import("node:fs/promises");
  const text = await readFile(path, "utf8");
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i === -1) continue;
    const k = t.slice(0, i).trim();
    const v = t.slice(i + 1).trim();
    if (!process.env[k]) process.env[k] = v;
  }
}

function getProvider(chainKey) {
  const chain = CHAINS[chainKey];
  if (!chain) throw new Error(`Unknown chain: ${chainKey}`);
  const url =
    process.env[chain.rpcEnv]?.trim() || PUBLIC_RPC[chainKey];
  const network = Network.from(chain.id);
  return new JsonRpcProvider(url, network, { staticNetwork: network });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    chain: DEFAULT_CHAIN,
    pool: null,
    hours: 6,
    fromBlock: null,
    toBlock: null,
    outDir: DATA_DIR,
  };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--chain") out.chain = args[++i];
    else if (a === "--pool") out.pool = args[++i];
    else if (a === "--hours") out.hours = Number(args[++i]);
    else if (a === "--from-block") out.fromBlock = Number(args[++i]);
    else if (a === "--to-block") out.toBlock = Number(args[++i]);
    else if (a === "--out-dir") out.outDir = args[++i];
    else if (a === "--help" || a === "-h") {
      console.log(`Usage: npm run fetch -- [options]
  --chain ethereum            (default: ethereum)
  --pool 0x...                (default: chain USDC/USDT pool)
  --hours 6                   block range = hours back from latest
  --from-block N --to-block M explicit range
  --out-dir ../data`);
      process.exit(0);
    }
  }
  const chain = CHAINS[out.chain];
  out.pool = getAddress(out.pool || chain.defaultPool);
  return out;
}

function minuteKey(tsSec) {
  const d = new Date(tsSec * 1000);
  d.setUTCSeconds(0, 0);
  return d.toISOString().slice(0, 19).replace("T", " ");
}

function dayKey(tsSec) {
  return new Date(tsSec * 1000).toISOString().slice(0, 10);
}

function emptyMinute() {
  return {
    netAmount0: 0n,
    netAmount1: 0n,
    inAmount0: 0n,
    inAmount1: 0n,
    openTick: null,
    closeTick: null,
    lowestTick: null,
    highestTick: null,
    currentLiquidity: 0n,
  };
}

function ingestMinute(buckets, tsSec, parsed) {
  const key = minuteKey(tsSec);
  if (!buckets.has(key)) buckets.set(key, emptyMinute());
  const m = buckets.get(key);
  const amount0 = BigInt(parsed.amount0);
  const amount1 = BigInt(parsed.amount1);
  const liquidity = BigInt(parsed.liquidity);

  m.netAmount0 += amount0;
  m.netAmount1 += amount1;
  if (amount0 > 0n) m.inAmount0 += amount0;
  else m.inAmount0 += -amount0;
  if (amount1 > 0n) m.inAmount1 += amount1;
  else m.inAmount1 += -amount1;

  const t = Number(parsed.tick);
  if (m.openTick === null) m.openTick = t;
  m.closeTick = t;
  m.lowestTick =
    m.lowestTick === null ? t : Math.min(m.lowestTick, t);
  m.highestTick =
    m.highestTick === null ? t : Math.max(m.highestTick, t);
  m.currentLiquidity = liquidity;
}

function toCsvRow(timestamp, m) {
  const tick = (v) => (v === null ? 0 : v);
  return [
    timestamp,
    m.netAmount0.toString(),
    m.netAmount1.toString(),
    tick(m.closeTick),
    tick(m.openTick),
    tick(m.lowestTick),
    tick(m.highestTick),
    m.inAmount0.toString(),
    m.inAmount1.toString(),
    m.currentLiquidity.toString(),
  ].join(",");
}

async function fetchLogs(provider, pool, fromBlock, toBlock) {
  const all = [];
  for (let start = fromBlock; start <= toBlock; start += CHUNK_SIZE) {
    const end = Math.min(start + CHUNK_SIZE - 1, toBlock);
    process.stderr.write(`  logs ${start} → ${end}\n`);
    const logs = await provider.getLogs({
      address: pool,
      topics: [SWAP_TOPIC],
      fromBlock: start,
      toBlock: end,
    });
    all.push(...logs);
  }
  return all;
}

async function main() {
  await loadEnvFileAsync();
  const opts = parseArgs();
  const provider = getProvider(opts.chain);
  const latest = await provider.getBlockNumber();

  let fromBlock = opts.fromBlock;
  let toBlock = opts.toBlock ?? latest;
  if (fromBlock == null) {
    const bt = BLOCK_TIME_SEC[opts.chain] ?? 12;
    const blocksBack = Math.ceil((opts.hours * 3600) / bt);
    fromBlock = Math.max(0, toBlock - blocksBack);
  }

  console.error(
    `ethers v6 fetch | chain=${opts.chain} pool=${opts.pool}\n` +
      `  blocks ${fromBlock} → ${toBlock} (~${opts.hours}h if default)`,
  );

  const logs = await fetchLogs(provider, opts.pool, fromBlock, toBlock);
  console.error(`  ${logs.length} Swap events`);

  const blockTs = new Map();
  const uniqueBlocks = [...new Set(logs.map((l) => l.blockNumber))];
  console.error(`  fetching ${uniqueBlocks.length} block timestamps…`);
  for (let i = 0; i < uniqueBlocks.length; i += 40) {
    const batch = uniqueBlocks.slice(i, i + 40);
    const blocks = await Promise.all(
      batch.map((n) => provider.getBlock(n)),
    );
    for (let j = 0; j < batch.length; j++) {
      const b = blocks[j];
      if (b?.timestamp) blockTs.set(batch[j], b.timestamp);
    }
  }

  const buckets = new Map();
  for (const log of logs) {
    const ts = blockTs.get(log.blockNumber);
    if (ts == null) continue;
    const parsed = SWAP_IFACE.parseLog({
      topics: log.topics,
      data: log.data,
    });
    if (!parsed) continue;
    const [, , amount0, amount1, , liquidity, tick] = parsed.args;
    ingestMinute(buckets, ts, { amount0, amount1, liquidity, tick });
  }

  mkdirSync(opts.outDir, { recursive: true });
  const poolLower = opts.pool.toLowerCase();
  const byDay = new Map();

  for (const [ts, m] of buckets) {
    const sec = Math.floor(new Date(ts + "Z").getTime() / 1000);
    const day = dayKey(sec);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push({ ts, m });
  }

  const header =
    "timestamp,netAmount0,netAmount1,closeTick,openTick,lowestTick,highestTick,inAmount0,inAmount1,currentLiquidity";

  for (const [day, rows] of byDay) {
    rows.sort((a, b) => a.ts.localeCompare(b.ts));
    const lines = [header, ...rows.map((r) => toCsvRow(r.ts, r.m))];
    const path = join(
      opts.outDir,
      `${opts.chain}-${poolLower}-${day}.minute.csv`,
    );
    writeFileSync(path, lines.join("\n") + "\n");
    console.error(`  wrote ${path} (${rows.length} minutes)`);
  }

  console.error("\nNext:");
  console.error(
    `  python3 scripts/run_depeg_lab.py --real --start YYYY-MM-DD --end YYYY-MM-DD`,
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
