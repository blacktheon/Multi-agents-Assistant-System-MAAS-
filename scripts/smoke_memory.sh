#!/usr/bin/env bash
# Post-smoke-test inspection helper. Run after the human smoke test to
# verify the memory layer and instrumentation worked.
set -euo pipefail
DB="${1:-data/store.db}"

echo "=== Recent user_facts ==="
sqlite3 "$DB" "SELECT id, author_agent, topic, fact_text, is_active
               FROM user_facts ORDER BY id DESC LIMIT 10;"
echo

echo "=== llm_usage rollup per agent + purpose (today) ==="
sqlite3 "$DB" "SELECT agent, purpose,
                      COUNT(*) AS calls,
                      SUM(input_tokens) AS total_in,
                      SUM(cache_creation_input_tokens) AS total_cc,
                      SUM(cache_read_input_tokens) AS total_cr,
                      SUM(output_tokens) AS total_out
               FROM llm_usage
               WHERE ts >= date('now')
               GROUP BY agent, purpose
               ORDER BY agent, purpose;"
echo

echo "=== Cache hit ratio (cache_read / (cache_read + input)) today ==="
sqlite3 "$DB" "SELECT agent,
                      CASE WHEN SUM(cache_read_input_tokens) + SUM(input_tokens) = 0
                           THEN 'n/a'
                           ELSE printf('%.1f%%',
                               100.0 * SUM(cache_read_input_tokens) /
                               (SUM(cache_read_input_tokens) + SUM(input_tokens)))
                      END AS hit_ratio
               FROM llm_usage WHERE ts >= date('now') GROUP BY agent;"
