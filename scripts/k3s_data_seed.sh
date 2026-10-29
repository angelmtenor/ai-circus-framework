#!/usr/bin/env bash
# Seed a data product's history through the *real* pipeline: data-api runs the scenario's
# simulator backfill (Kafka topics, landing files, ERP tables), data-ingest lands it in
# bronze. Uses the admin key (exported by the Makefile from .env — never printed): the admin
# tenant has no data of its own, so the history goes to the fallback org every tenant reads.
#
# Usage: scripts/k3s_data_seed.sh <data_product_slug> [days]
set -euo pipefail

SLUG="${1:?usage: k3s_data_seed.sh <data_product_slug> [days]}"
DAYS="${2:-}"
API="${DATA_API_URL:-http://data-api.localhost}"
KEY="${ADMIN_API_KEY:-angel2026}"
body='{}'
[ -n "$DAYS" ] && body="{\"days\": $DAYS}"

echo "▶ backfill of $SLUG via $API ..."
started=""
for _ in $(seq 1 20); do  # data-api may still be becoming routable right after a (re)start
    if curl -sf -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
        -d "$body" "$API/v1/$SLUG/simulator/backfill" >/dev/null; then started=1; break; fi
    sleep 3
done
[ -n "$started" ] || { echo "❌ data-api refused the backfill (is the core tier up? make k3s-data-core-up)"; exit 1; }

last=""
for _ in $(seq 1 360); do
    status=$(curl -sf -H "Authorization: Bearer $KEY" "$API/v1/$SLUG/overview" \
        | python3 -c 'import json,sys; b=(json.load(sys.stdin).get("simulator") or {}).get("backfill") or {}; print(b.get("state","?"), (b.get("progress") or [""])[-1], b.get("error",""), sep="|")')
    state="${status%%|*}"
    line="$(echo "$status" | cut -d'|' -f2)"
    [ "$line" != "$last" ] && [ -n "$line" ] && echo "  $line" && last="$line"
    case "$state" in
        done) echo "✓ history published — data-ingest is landing it (watch: kubectl -n ai-circus logs -f deploy/data-ingest)"; exit 0 ;;
        failed) echo "❌ backfill failed: $(echo "$status" | cut -d'|' -f3)"; exit 1 ;;
    esac
    sleep 5
done
echo "❌ backfill still running after 30 min"; exit 1
