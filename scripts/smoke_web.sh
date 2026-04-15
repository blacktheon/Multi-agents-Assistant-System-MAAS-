#!/usr/bin/env bash
# End-to-end sanity check for the Intelligence webapp. Seeds a fake report,
# spins up uvicorn, exercises the main routes, and cleans up. Manual only —
# not run in CI. Does not touch real data beyond the seeded file.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT=18080
REPORT_DATE="2099-12-31"
REPORT_PATH="data/intelligence/reports/${REPORT_DATE}.json"

mkdir -p "data/intelligence/reports" "data/intelligence/feedback"

cleanup() {
    rm -f "$REPORT_PATH"
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "seeding fake report at $REPORT_PATH"
cat > "$REPORT_PATH" <<'JSON'
{
  "date": "2099-12-31",
  "generated_at": "2099-12-31T08:00:00+08:00",
  "user_tz": "Asia/Shanghai",
  "watchlist_snapshot": ["smoke"],
  "news_items": [
    {
      "id": "n1",
      "headline": "SMOKE TEST HEADLINE",
      "summary": "smoke test summary",
      "importance": "high",
      "importance_reason": "smoke",
      "topics": ["smoke"],
      "source_tweets": [
        {
          "handle": "smoke",
          "url": "https://x.com/smoke/status/1",
          "text": "smoke",
          "posted_at": "2099-12-31T00:00:00Z"
        }
      ]
    }
  ],
  "suggested_accounts": [],
  "stats": {
    "tweets_fetched": 1,
    "handles_attempted": 1,
    "handles_succeeded": 1,
    "items_generated": 1,
    "errors": []
  }
}
JSON

echo "starting uvicorn on port $PORT"
uv run uvicorn \
    "project0.intelligence_web.app:_dev_factory" \
    --factory \
    --port "$PORT" \
    --host 127.0.0.1 \
    > /tmp/smoke_web.log 2>&1 &
SERVER_PID=$!

# Wait for the server to come up
for _ in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${PORT}/healthz" > /dev/null 2>&1; then
        break
    fi
    sleep 0.3
done

echo "GET /healthz"
curl -sf "http://127.0.0.1:${PORT}/healthz"
echo

echo "GET /reports/${REPORT_DATE}"
curl -sf "http://127.0.0.1:${PORT}/reports/${REPORT_DATE}" | grep -q "SMOKE TEST HEADLINE" \
    && echo "  ok headline rendered" \
    || { echo "  FAIL headline missing"; exit 1; }

echo "GET /history"
curl -sf "http://127.0.0.1:${PORT}/history" | grep -q "${REPORT_DATE}" \
    && echo "  ok date listed" \
    || { echo "  FAIL date missing from history"; exit 1; }

echo "POST /api/feedback/thumbs"
curl -sf -X POST "http://127.0.0.1:${PORT}/api/feedback/thumbs" \
    -H "Content-Type: application/json" \
    -d "{\"report_date\":\"${REPORT_DATE}\",\"item_id\":\"n1\",\"score\":1}" \
    | grep -q '"ok":true' \
    && echo "  ok thumbs accepted" \
    || { echo "  FAIL thumbs rejected"; exit 1; }

echo "GET /reports/${REPORT_DATE} (check feedback reflected)"
curl -sf "http://127.0.0.1:${PORT}/reports/${REPORT_DATE}" | grep -q "thumb-up active" \
    && echo "  ok thumbs-up reflected in rendered HTML" \
    || { echo "  FAIL thumbs-up not active in HTML"; exit 1; }

echo
echo "smoke test passed"
