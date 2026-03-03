#!/usr/bin/env bash
# Backs up model_router_real to backups/model_router_real_<timestamp>.sql
set -euo pipefail

BACKUP_DIR="$(dirname "$0")/../backups"
mkdir -p "$BACKUP_DIR"

FILENAME="model_router_real_$(date +%Y%m%d_%H%M%S).sql"
OUTPUT="$BACKUP_DIR/$FILENAME"

echo "Backing up model_router_real → $OUTPUT"
docker compose exec db pg_dump -U router model_router_real > "$OUTPUT"
echo "Done. $(wc -c < "$OUTPUT" | tr -d ' ') bytes written."
