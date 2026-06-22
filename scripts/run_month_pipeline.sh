#!/usr/bin/env bash
# Fetch oracle + BigQuery swaps for one calendar month, prepare, run mainnet backtest.
#
# Usage:
#   export BIGQUERY_AUTH_FILE=/absolute/path/to/gcp-service-account.json
#   ./scripts/run_month_pipeline.sh 2026-05
#
# Requires: venv with pip install -r requirements.txt (demeter-fetch on PATH via venv)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MONTH="${1:-}"
if [[ -z "$MONTH" || ! "$MONTH" =~ ^[0-9]{4}-[0-9]{2}$ ]]; then
  echo "Usage: $0 YYYY-MM   (example: $0 2026-05)"
  exit 1
fi

if [[ -z "${BIGQUERY_AUTH_FILE:-}" ]]; then
  echo "Set BIGQUERY_AUTH_FILE to your Google Cloud service-account JSON path."
  exit 1
fi

YEAR="${MONTH%%-*}"
MON="${MONTH##*-}"
# Last day of month (macOS/BSD date)
END_DAY="$(date -j -f "%Y-%m-%d" "${MONTH}-01" "+%Y-%m-%d" -v+1m -v-1d 2>/dev/null || date -d "${MONTH}-01 +1 month -1 day" "+%Y-%m-%d")"

START_DATE="${MONTH}-01"
END_DATE="$END_DAY"
ORACLE_CSV="data/chainlink_usdc_${YEAR}-${MON}.csv"
PREPARED="data/prepared_swaps_${YEAR}-${MON}.csv"
POOL="0x3416cf6c708da44db2624d63ea0aaef7113527c6"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

echo "=== Month: $MONTH ($START_DATE → $END_DATE) ==="

echo "Step 1/3: Chainlink (Dune) + Uniswap minutes (BigQuery)..."
"$PYTHON" scripts/fetch_data.py \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --oracle-out "$ORACLE_CSV" \
  --pool-out-dir data \
  --bigquery-auth-file "$BIGQUERY_AUTH_FILE"

echo "Step 2/3: Merge oracle + classify drains..."
"$PYTHON" scripts/prepare_data.py \
  --use-minute-files \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --pool "$POOL" \
  --oracle "$ORACLE_CSV" \
  --out "$PREPARED"

echo "Step 3/3: Backtest (static vs piecewise vs quadratic)..."
MPLBACKEND=Agg "$PYTHON" scripts/backtest_mainnet.py \
  --prepared "$PREPARED" \
  --chart-out "output/oscillon_backtest_${YEAR}-${MON}.png" \
  --timeline-csv "output/depeg_fee_timeline_${YEAR}-${MON}.csv" \
  --timeline-out "output/depeg_fee_timeline_${YEAR}-${MON}.png"

echo "Done."
echo "  Oracle:   $ORACLE_CSV"
echo "  Prepared: $PREPARED"
echo "  Charts:   output/oscillon_backtest_${YEAR}-${MON}.png"
