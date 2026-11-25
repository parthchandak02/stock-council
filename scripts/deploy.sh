#!/usr/bin/env bash
# Deploy stock-council: checks on a copy of the live data first; any failure stops the deploy.
#   1. page sweep: every run, stock and Screener page renders, India and US
#   2. UX check (design-principles skill): phone + desktop, dark + light, clipping, tap sizes, flows
#   3. push, pull on the server, restart both sites, check them live
set -euo pipefail
cd "$(dirname "$0")/.."
TMP=$(mktemp -d /tmp/sc-deploy.XXXX)
stop() { for p in 8398 8399; do pid=$(ss -ltnp | grep ":$p " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1); [ -n "$pid" ] && kill "$pid"; done; rm -rf "$TMP"; }
trap stop EXIT
echo "copying data..."
sqlite3 /srv/stock-council/data/stock-council.db ".backup $TMP/stock-council.db"
sqlite3 /srv/stock-council/data/us.db ".backup $TMP/us.db"
cp /srv/stock-council/data/*.pkl "$TMP/" 2>/dev/null || true
SC_DATA_DIR=$TMP uv run python scripts/page_sweep.py
SC_DATA_DIR=$TMP SC_MARKET=us uv run python scripts/page_sweep.py
SC_DATA_DIR=$TMP SC_US_UPSTREAM=http://127.0.0.1:8399 nohup uv run uvicorn stock_council.web.app:app --port 8398 >"$TMP/in.log" 2>&1 &
SC_DATA_DIR=$TMP SC_MARKET=us nohup uv run uvicorn stock_council.web.app:app --port 8399 >"$TMP/us.log" 2>&1 &
sleep 8; curl -s -o /dev/null --max-time 120 http://127.0.0.1:8398/screener; curl -s -o /dev/null --max-time 120 http://127.0.0.1:8399/screener
node ~/.claude/skills/design-principles/scripts/ux-check.mjs --config ux-check.json
git push -q origin main
(cd /srv/stock-council && git pull -q --ff-only)
sudo systemctl restart stock-council-web stock-council-us-web
sleep 25
for u in / /screener /us/ /us/screener /settings; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 120 "http://127.0.0.1:8306$u"); echo "live $u $code"
  [ "$code" = 200 ] || { echo "LIVE CHECK FAILED: $u"; exit 1; }
done
echo "deployed $(git rev-parse --short HEAD)"
