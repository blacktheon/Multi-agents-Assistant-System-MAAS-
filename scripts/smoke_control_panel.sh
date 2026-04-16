#!/usr/bin/env bash
set -euo pipefail

# Prepares the environment for the control-panel final human smoke test
# described in docs/superpowers/specs/2026-04-16-control-panel-design.md §10.8.

DB="data/store.db"

if [ ! -f "$DB" ]; then
    echo "== no store.db yet — panel will create it on startup =="
else
    echo "== store state =="
    sqlite3 "$DB" "SELECT 'user_facts:' AS t, COUNT(*) FROM user_facts UNION ALL SELECT 'llm_usage:', COUNT(*) FROM llm_usage;" 2>/dev/null || echo "(tables not yet created)"

    echo
    echo "== recent llm_usage (rollup by agent) =="
    sqlite3 "$DB" "SELECT agent, purpose, COUNT(*), SUM(input_tokens+cache_creation_input_tokens+cache_read_input_tokens), SUM(output_tokens) FROM llm_usage GROUP BY agent, purpose ORDER BY 2;" 2>/dev/null || echo "(no data)"

    echo
    echo "== active user_facts =="
    sqlite3 "$DB" "SELECT id, ts, author_agent, fact_text, topic, is_active FROM user_facts WHERE is_active=1 ORDER BY id DESC;" 2>/dev/null || echo "(no data)"
fi

echo
echo "panel: uv run python -m project0.control_panel  (port 8090)"
echo "maas:  click Start in the panel"
