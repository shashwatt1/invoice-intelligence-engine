#!/usr/bin/env bash
# Replace ALL data in one VM environment's database with the data from a Supabase backup.
# Usage: restore_data.sh staging|production /opt/edihor/migration/backup-<ts>
# (a folder written by supabase_backup.sh). Calls that need no input read /dev/null, so this can be
# streamed with `bash -s` without swallowing the rest of the caller's script (seen at cutover, 2026-11-13).
# The schema is NOT touched (it already matches, built by the same migrations).
# One transaction: truncate every public table + load the dump's data. Any error -> nothing changes.
set -euo pipefail

ENV_NAME="${1:?staging or production}"
BACKUP="${2:?backup folder}"
case "$ENV_NAME" in
  staging)    PROJECT=invoice-staging; PORT=8101 ;;
  production) PROJECT=invoice-prod;    PORT=8100 ;;
  *) echo "unknown environment: $ENV_NAME" >&2; exit 1 ;;
esac
DB=$PROJECT-db
DUMP="$BACKUP/supabase-full.dump"
test -s "$DUMP" && test -s "$BACKUP/row-counts-supabase.txt"
(cd "$BACKUP" && sha256sum -c --quiet SHA256SUMS)
psql_db() { sudo -n docker exec -i "$DB" psql -U invoice -d invoice -X -v ON_ERROR_STOP=1 "$@"; }

echo "== $ENV_NAME: backup $(basename "$BACKUP") (checksums OK)"
echo "-- migration in backup vs $ENV_NAME:"
grep -A2 "^COPY public.alembic_version" <(sudo -n docker run --rm -i postgres:17-alpine pg_restore --data-only -t alembic_version -f - < "$DUMP") | sed -n 2p
psql_db -A -t -c "select version_num from alembic_version" < /dev/null

echo "-- stopping the $ENV_NAME app"
(cd /opt/edihor/$ENV_NAME && sudo -n docker compose stop app) 2>&1 | tail -1

echo "-- loading (one transaction)"
TRUNC=$(psql_db -A -t -c "select 'TRUNCATE ' || string_agg(format('public.%I', tablename), ', ') || ' CASCADE;' from pg_tables where schemaname = 'public'" < /dev/null)
{
  echo "BEGIN;"
  echo "$TRUNC"
  sudo -n docker run --rm -i postgres:17-alpine pg_restore --data-only -n public --disable-triggers --no-owner --no-privileges -f - < "$DUMP"
  echo "COMMIT;"
} | psql_db -q > /tmp/restore-$ENV_NAME.log 2>&1 || { echo "LOAD FAILED - rolled back, nothing changed. Log: /tmp/restore-$ENV_NAME.log"; tail -5 /tmp/restore-$ENV_NAME.log; (cd /opt/edihor/$ENV_NAME && sudo -n docker compose start app) >/dev/null 2>&1; exit 1; }
rm -f /tmp/restore-$ENV_NAME.log
echo "loaded"

echo "-- row counts: Supabase vs $ENV_NAME"
psql_db -A -t -F "|" < "$BACKUP/row-counts.sql" > /tmp/counts-$ENV_NAME.txt
if diff -q "$BACKUP/row-counts-supabase.txt" /tmp/counts-$ENV_NAME.txt >/dev/null; then
  echo "ALL $(wc -l < /tmp/counts-$ENV_NAME.txt) TABLES MATCH ($(awk -F'|' '{s+=$2} END {print s}' /tmp/counts-$ENV_NAME.txt) rows)"
else
  echo "MISMATCH:"; diff "$BACKUP/row-counts-supabase.txt" /tmp/counts-$ENV_NAME.txt
fi
rm -f /tmp/counts-$ENV_NAME.txt

echo "-- starting the $ENV_NAME app"
(cd /opt/edihor/$ENV_NAME && sudo -n docker compose up -d --wait app) 2>&1 | tail -1
curl -s -o /dev/null -w "health HTTP %{http_code}\n" http://127.0.0.1:$PORT/api/v1/health
