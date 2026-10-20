#!/usr/bin/env bash
# End-to-end smoke test against a running stack (`make smoke` brings it up first):
# sign up over HTTP, run the ping workflow, poll to completion, then check Jaeger for one trace
# containing both the HTTP span and the activity span.
set -euo pipefail

API="${API_URL:-http://localhost:8080}"
JAEGER="${JAEGER_URL:-http://localhost:16686}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }
fail() { echo "SMOKE FAILED: $*" >&2; exit 1; }

EMAIL="smoke-$(date +%s)-$RANDOM@example.com"
echo "1. signup as $EMAIL"
SIGNUP=$(curl -fsS -c "$JAR" -H 'Content-Type: application/json' -X POST "$API/v1/auth/signup" -d @- <<JSON
{"email": "$EMAIL", "password": "smoke-test-password-1", "display_name": "Smoke",
 "org_name": "Smoke Org", "location_name": "Smoke Location", "timezone": "America/New_York"}
JSON
)
CSRF=$(echo "$SIGNUP" | json "d['csrf_token']")
ORG_NAME=$(echo "$SIGNUP" | json "d['org']['name']")

echo "2. me + locations"
curl -fsS -b "$JAR" "$API/v1/auth/me" | json "d['user']['email']" | grep -qx "$EMAIL" || fail "me"
curl -fsS -b "$JAR" "$API/v1/locations" | json "len(d['data'])" | grep -qx 1 || fail "locations"

echo "3. start ping workflow"
WF=$(curl -fsS -b "$JAR" -H "X-CSRF-Token: $CSRF" -X POST "$API/v1/dev/ping-workflow" | json "d['workflow_id']")
echo "   workflow_id=$WF"

echo "4. poll until completed"
for _ in $(seq 1 60); do
  RES=$(curl -fsS -b "$JAR" "$API/v1/dev/ping-workflow/$WF")
  STATUS=$(echo "$RES" | json "d['status']")
  [ "$STATUS" = "COMPLETED" ] && break
  sleep 1
done
[ "$STATUS" = "COMPLETED" ] || fail "workflow status is $STATUS"
echo "$RES" | json "d['result']['org_name']" | grep -qx "$ORG_NAME" || fail "unexpected workflow result: $RES"

echo "5. look for the trace in Jaeger"
for _ in $(seq 1 30); do
  if curl -fsS "$JAEGER/api/traces?service=chefos-api&limit=50&lookback=1h" | python3 -c '
import sys, json
traces = json.load(sys.stdin)["data"]
for t in traces:
    ops = {s["operationName"] for s in t["spans"]}
    has_http = any("ping-workflow" in o and o.startswith("POST") for o in ops)
    has_activity = any(o.startswith("RunActivity") and "ping_activity" in o for o in ops)
    has_sql = any(s.get("tags") and any(tag["key"] == "db.system" or tag["key"] == "db.system.name" for tag in s["tags"]) for s in t["spans"])
    if has_http and has_activity and has_sql:
        print("trace", t["traceID"], "spans", len(t["spans"]))
        sys.exit(0)
sys.exit(1)
'; then
    echo "SMOKE OK"
    exit 0
  fi
  sleep 1
done
fail "no Jaeger trace with HTTP + activity + SQL spans for service chefos-api"
