#!/usr/bin/env bash
# Run estimate-collect in batches until done or bearer_rejected.
# Usage: bash scripts/run_estimate_collect.sh [--reset] [--force]
set -euo pipefail

KEY=$(grep '^CRM_SYNC_SECRET=' secrets/keys.local.env 2>/dev/null | cut -d= -f2- | tr -d $'\r\n')
if [ -z "$KEY" ]; then echo "CRM_SYNC_SECRET not found in secrets/keys.local.env"; exit 1; fi

BASE="https://crm.collaborativeconceptsfl.com"
RESET="false"
FORCE="false"
for arg in "$@"; do
  case "$arg" in --reset) RESET="true";; --force) FORCE="true";; esac
done

echo "Starting estimate-collect (reset=$RESET force=$FORCE)"
PASS=0
while true; do
  PASS=$((PASS+1))
  PAYLOAD="{\"n\":25,\"budget\":55,\"reset\":$RESET,\"force\":$FORCE}"
  RESET="false"  # only reset on first pass
  RESP=$(curl -s -X POST "$BASE/sync/estimate-collect?k=$KEY" \
    -H "Content-Type: application/json" -d "$PAYLOAD" --max-time 90)
  echo "=== Pass $PASS ==="
  echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
  DONE=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('done','?'))" 2>/dev/null)
  BEARER=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bearer_rejected',False))" 2>/dev/null)
  if [ "$BEARER" = "True" ]; then
    echo ""
    echo "❌ AccuLynx Estimatev3 API rejected Bearer auth."
    echo "   Use the 'AccuLynx Estimate Lines → CRM' bookmarklet on a logged-in AccuLynx tab."
    exit 2
  fi
  if [ "$DONE" = "True" ]; then
    echo ""
    echo "✓ estimate-collect complete."
    break
  fi
done
