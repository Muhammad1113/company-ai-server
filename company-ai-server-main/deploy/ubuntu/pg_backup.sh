#!/usr/bin/env bash
# PostgreSQL backup (docker compose, production). Cron/timer se roz chalao; backups ko DOOSRI machine par bhi bhejo + encrypt karo.
#   ./deploy/ubuntu/pg_backup.sh [dest-dir] [keep]
set -euo pipefail
cd "$(dirname "$0")/../.."
DEST="${1:-backups}"; KEEP="${2:-14}"
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/pg-$STAMP.dump"
docker compose exec -T postgres pg_dump -U company_ai -d company_ai -Fc > "$OUT"
SIZE="$(stat -c %s "$OUT")"
[ "$SIZE" -gt 1024 ] || { echo "Backup suspicious chhota ($SIZE bytes)"; exit 1; }
# data volume (regulatory archive + system prompt)
docker compose exec -T api tar -C /app/data -cf - . > "$DEST/appdata-$STAMP.tar" || echo "WARN: appdata backup fail"
ls -1t "$DEST"/pg-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
ls -1t "$DEST"/appdata-*.tar | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "OK: $OUT ($SIZE bytes)"
