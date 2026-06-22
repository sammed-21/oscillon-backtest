/** @typedef {{ id: number, rpcEnv: string, defaultPool: string }} ChainConfig */

/** Ethereum mainnet — USDC/USDT 0.01% Uniswap v3 pool */
/** @type {Record<string, ChainConfig>} */
export const CHAINS = {
  ethereum: {
    id: 1,
    rpcEnv: "ETHEREUM_RPC_URL",
    defaultPool: "0x3416cf6c708da44db2624d63ea0aaef7113527c6",
  },
};

export const DEFAULT_CHAIN = "ethereum";

export const PUBLIC_RPC = {
  ethereum: "https://ethereum-rpc.publicnode.com",
};
