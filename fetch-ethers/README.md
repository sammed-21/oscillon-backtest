# Fetch minute swaps (Ethereum mainnet, ethers v6)

```bash
cp .env.example .env   # set ETHEREUM_RPC_URL
npm install
npm run fetch -- --chain ethereum --hours 24
npm run oracle         # live Chainlink USDC/USD on ETH mainnet
```

**Pool:** `0x3416cf6c708da44db2624d63ea0aaef7113527c6` (USDC/USDT 0.01%)

Writes `../data/ethereum-0x3416cf6c708da44db2624d63ea0aaef7113527c6-*.minute.csv`.

For bulk history, use `scripts/fetch_data.py` (Dune oracle + BigQuery via demeter-fetch).
