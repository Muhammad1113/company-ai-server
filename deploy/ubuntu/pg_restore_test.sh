#!/usr/bin/env bash
# Restore ka TEST: naye dump ko scratch database (restore_check) mein bahal karke ginti milata hai. Asli DB ko haath nahi lagata.
#   ./deploy/ubuntu/pg_restore_test.sh [dump-file]
set -euo pipefail
cd "$(dirname "$0")/../.."
FILE="${1:-$(ls -1t backups/pg-*.dump | head -n1)}"
[ -f "$FILE" ] || { echo "dump nahi mila"; exit 1; }
echo "Restore test: $FILE"
P="docker compose exec -T postgres"
$P psql -U company_ai -d postgres -c "DROP DATABASE IF EXISTS restore_check" >/dev/null
$P psql -U company_ai -d postgres -c "CREATE DATABASE restore_check" >/dev/null
$P pg_restore -U company_ai -d restore_check --no-owner < "$FILE"
bad=0
printf '%-18s %10s %10s\n' table live restored
for t in companies users sessions conversations messages rag_chunks reg_versions openfda_snapshot usage_log; do
  live=$($P psql -U company_ai -d company_ai -tA -c "SELECT count(*) FROM $t" 2>/dev/null | tr -d '\r' || echo -1)
  rest=$($P psql -U company_ai -d restore_check -tA -c "SELECT count(*) FROM $t" 2>/dev/null | tr -d '\r' || echo -1)
  live=${live:--1}; rest=${rest:--1}
  flag=""; if [ "$live" -gt 0 ] && [ "$rest" -le 0 ]; then flag="  <-- RESTORE KHALI"; bad=1; fi
  printf '%-18s %10s %10s%s\n' "$t" "$live" "$rest" "$flag"
done
$P psql -U company_ai -d postgres -c "DROP DATABASE IF EXISTS restore_check" >/dev/null
[ "$bad" -eq 0 ] && echo "RESTORE TEST PASS" || { echo "RESTORE TEST FAIL"; exit 1; }
