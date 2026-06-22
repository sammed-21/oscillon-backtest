#!/usr/bin/env node
/** Read live Uniswap v3 pool state with ethers v6. */

import { Contract, JsonRpcProvider, Network } from "ethers";
import { CHAINS, DEFAULT_CHAIN, PUBLIC_RPC } from "./chains.mjs";

const POOL_ABI = [
  "function token0() view returns (address)",
  "function token1() view returns (address)",
  "function fee() view returns (uint24)",
  "function liquidity() view returns (uint128)",
  "function slot0() view returns (uint160 sqrtPriceX96, int24 tick, uint16, uint16, uint16, uint8, bool)",
];

const ERC20_ABI = [
  "function symbol() view returns (string)",
  "function decimals() view returns (uint8)",
];

function tickToPrice(tick) {
  return 1.0001 ** Number(tick);
}

async function main() {
  const chainKey = DEFAULT_CHAIN;
  const chain = CHAINS[chainKey];
  const pool = process.argv.find((a) => a.startsWith("0x")) || chain.defaultPool;
  const url = process.env[chain.rpcEnv]?.trim() || PUBLIC_RPC[chainKey];
  const network = Network.from(chain.id);
  const provider = new JsonRpcProvider(url, network, { staticNetwork: network });
  const poolC = new Contract(pool, POOL_ABI, provider);

  const [t0, t1, fee, liq, slot0] = await Promise.all([
    poolC.token0(),
    poolC.token1(),
    poolC.fee(),
    poolC.liquidity(),
    poolC.slot0(),
  ]);
  const tick = Number(slot0[1]);
  const c0 = new Contract(t0, ERC20_ABI, provider);
  const sym0 = await c0.symbol();
  const dec0 = await c0.decimals();

  console.log(JSON.stringify({
    chain: chainKey,
    pool,
    token0: sym0,
    feeTier: Number(fee),
    liquidity: liq.toString(),
    tick,
    priceToken1PerToken0: tickToPrice(tick),
    tvlNote: "Use balanceOf for full TVL",
  }, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
