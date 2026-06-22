#!/usr/bin/env node
/**
 * Read Chainlink USDC/USD on Ethereum mainnet (ethers v6).
 * Same feed as scripts/fetch_data.py Dune query.
 */

import { Contract, JsonRpcProvider, Network, formatUnits } from "ethers";
import { CHAINS, DEFAULT_CHAIN, PUBLIC_RPC } from "./chains.mjs";

const AGGREGATOR_ABI = [
  "function latestRoundData() view returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)",
  "function decimals() view returns (uint8)",
];

/** Chainlink USDC/USD on Ethereum mainnet */
const ETH_USDC_USD = "0x8fffffd4afb6115b954bd326cbe7b4ba576818f6";

async function main() {
  const chain = CHAINS[DEFAULT_CHAIN];
  const url = process.env[chain.rpcEnv]?.trim() || PUBLIC_RPC[DEFAULT_CHAIN];
  const network = Network.from(chain.id);
  const provider = new JsonRpcProvider(url, network, { staticNetwork: network });
  const feed = new Contract(ETH_USDC_USD, AGGREGATOR_ABI, provider);
  const [dec, round] = await Promise.all([
    feed.decimals(),
    feed.latestRoundData(),
  ]);
  const price = Number(formatUnits(round.answer, dec));
  const depegBps = Math.round(Math.abs(1 - price) * 10_000);
  console.log(
    JSON.stringify(
      {
        chain: DEFAULT_CHAIN,
        feed: ETH_USDC_USD,
        priceUsd: price,
        depegBps,
        updatedAt: Number(round.updatedAt),
      },
      null,
      2,
    ),
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
