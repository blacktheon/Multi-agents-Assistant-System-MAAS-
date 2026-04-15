#!/usr/bin/env bash
# Run the Intelligence webapp in isolation with live reload for template/CSS
# iteration. Reads the real data/intelligence/reports/ directory. Does not
# start the Telegram bots. Bound on a separate port from production (8081).
set -euo pipefail

cd "$(dirname "$0")/.."

exec uv run uvicorn \
    "project0.intelligence_web.app:_dev_factory" \
    --factory \
    --reload \
    --port 8081 \
    --host 127.0.0.1
