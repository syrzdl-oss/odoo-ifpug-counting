#!/usr/bin/env bash
# run_updates.sh - materialise the IFPUG functions in a GraphDB repository.
# Sends sparql/update/*.ru in name order to the repository's statements endpoint.
# Usage (from the repository root):
#   bash run/run_updates.sh [ENDPOINT]
# Default ENDPOINT: http://localhost:7200/repositories/odoo_fsm/statements
set -euo pipefail
ENDPOINT="${1:-http://localhost:7200/repositories/odoo_fsm/statements}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for f in "$ROOT"/sparql/update/*.ru; do
  start=$(date +%s)
  code=$(curl -s -o /tmp/run_updates_resp.txt -w "%{http_code}" -X POST \
         -H "Content-Type: application/sparql-update; charset=utf-8" \
         --data-binary @"$f" --max-time 3600 "$ENDPOINT")
  if [[ "$code" =~ ^2 ]]; then
    printf "OK    %-28s %5ss\n" "$(basename "$f")" "$(( $(date +%s) - start ))"
  else
    printf "FAIL  %s (HTTP %s)\n" "$(basename "$f")" "$code"; cat /tmp/run_updates_resp.txt; exit 1
  fi
done
echo "All updates done. Run sparql/query/S1.rq ... S10.rq to check the results."
