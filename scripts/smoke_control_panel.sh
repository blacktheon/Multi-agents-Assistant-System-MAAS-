#!/usr/bin/env bash
set -euo pipefail

# Prepares the environment for the control-panel final human smoke test
# described in docs/superpowers/specs/2026-04-16-control-panel-design.md §10.8.

echo "== store state =="
sqlite3 data/store.db "SELECT 'user_facts:' AS t, COUNT(*) FROM user_facts UNION ALL SELECT 'llm_usage:', COUNT(*) FROM llm_usage;"

echo
echo "== recent llm_usage (rollup by agent) =="
sqlite3 data/store.db "SELECT agent, purpose, COUNT(*), SUM(input_tokens+cache_creation_input_tokens+cache_read_input_tokens), SUM(output_tokens) FROM llm_usage GROUP BY agent, purpose ORDER BY 2;"

echo
echo "== active user_facts =="
sqlite3 data/store.db "SELECT id, ts, author_agent, fact_text, topic, is_active FROM user_facts WHERE is_active=1 ORDER BY id DESC;"

echo
echo "panel: uv run python -m project0.control_panel  (port 8090)"
echo "maas:  click Start in the panel"
