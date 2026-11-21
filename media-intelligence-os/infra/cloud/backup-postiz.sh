#!/usr/bin/env bash
# Nightly backup of the VM's Postiz database (cron: README step 8). Keeps 14 days.
set -euo pipefail
OUT=/var/backups/postiz
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker exec postiz-runtime-postgres pg_dump -U "${POSTIZ_DB_USER:-postiz-user}" \
  -d "${POSTIZ_DB_NAME:-postiz-db-local}" -Fc > "$OUT/postiz-$stamp.dump"
chmod 600 "$OUT/postiz-$stamp.dump"
find "$OUT" -name 'postiz-*.dump' -mtime +14 -delete
