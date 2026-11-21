#!/usr/bin/env bash
# Restore the exported Postiz on the VM and start it (run as `ubuntu` in /opt/postiz, which holds
# docker-compose.yaml, .env, postiz-cloud.override.yaml, postiz-db.dump and postiz-uploads.tgz).
#
#   bash restore-postiz.sh
#
# Safe to re-run before you've started using the VM: it refuses to overwrite a database that
# already has data. Nothing secret is printed.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f docker-compose.yaml -f postiz-cloud.override.yaml)
DB_USER="${POSTIZ_DB_USER:-postiz-user}"
DB_NAME="${POSTIZ_DB_NAME:-postiz-db-local}"

for f in docker-compose.yaml .env postiz-cloud.override.yaml postiz-db.dump postiz-uploads.tgz; do
  [[ -f "$f" ]] || { echo "Missing $f in $(pwd)"; exit 1; }
done
grep -q '^POSTIZ_PUBLIC_URL=https://' .env || {
  echo "Add POSTIZ_PUBLIC_URL=https://<vm-name>.<tailnet>.ts.net to .env first (README step 5)."
  exit 1
}

echo "Checking every image has an ARM (arm64) build..."
"${COMPOSE[@]}" pull --quiet

echo "Starting the database..."
"${COMPOSE[@]}" up -d postiz-postgres postiz-redis
for _ in $(seq 1 30); do
  "${COMPOSE[@]}" exec -T postiz-postgres pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1 && break
  sleep 2
done

tables=$("${COMPOSE[@]}" exec -T postiz-postgres psql -U "$DB_USER" -d "$DB_NAME" -At \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
if [[ "$tables" != "0" ]]; then
  echo "The VM's Postiz database already has tables; not overwriting it."
else
  echo "Restoring the database..."
  "${COMPOSE[@]}" exec -T postiz-postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner \
    < postiz-db.dump
fi

echo "Restoring uploads..."
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh postiz -c 'tar -xzf - -C /uploads' \
  < postiz-uploads.tgz

echo "Starting Postiz (Temporal starts fresh)..."
"${COMPOSE[@]}" up -d
echo "Waiting for Postiz to be healthy (first start takes a few minutes)..."
for _ in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' postiz-runtime-app 2>/dev/null || true)
  [[ "$status" == "healthy" ]] && { echo "Postiz is up."; break; }
  sleep 10
done
echo "Next: README step 6 (tailscale serve) and step 7 (point Media OS at the VM)."
