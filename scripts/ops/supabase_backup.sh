#!/usr/bin/env bash
# Read-only backup of the Supabase database, run on the VM.
# Needs /opt/edihor/migration/supabase.env (PGURL=..., mode 600). Never prints the URL.
# Output: /opt/edihor/migration/backup-<UTC timestamp>/
set -uo pipefail

ENVF=/opt/edihor/migration/supabase.env
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/opt/edihor/migration/backup-$TS
umask 077
mkdir -p "$OUT"
mask() { sed -E 's#[^ "]*(@|://|%)[^ "]*#[masked]#g'; }
pg() {  # pg <command...> : run a Postgres 17 client tool against Supabase, output files in $OUT
  sudo -n docker run --rm --env-file "$ENVF" -u "$(id -u):$(id -g)" -v "$OUT":/out -w /out \
    postgres:17-alpine "$@"
}

echo "backup folder: $OUT"

# 1. Full backup: schema + data of the whole database (custom format, restorable with pg_restore)
pg sh -c 'pg_dump "$PGURL" -Fc --no-owner --no-privileges -f supabase-full.dump' 2>&1 | mask | tail -5
echo "full dump exit: ${PIPESTATUS[0]}"

# 2. The app's schema alone, as readable SQL
pg sh -c 'pg_dump "$PGURL" --schema-only -n public --no-owner --no-privileges -f supabase-public-schema.sql' 2>&1 | mask | tail -5
echo "schema dump exit: ${PIPESTATUS[0]}"

# 3. Exact row count of every table in the app's schema
cat > "$OUT/row-counts.sql" <<'SQL'
select table_name,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text
from information_schema.tables
where table_schema = 'public' and table_type = 'BASE TABLE'
order by 1;
SQL
pg sh -c 'psql "$PGURL" -X -A -t -F "|" -v ON_ERROR_STOP=1 -f row-counts.sql > row-counts-supabase.txt' 2>&1 | mask | tail -5
echo "row counts exit: ${PIPESTATUS[0]}"

# 4. Check the dump is readable and list what it holds
pg sh -c 'pg_restore --list supabase-full.dump > dump-contents.txt' 2>&1 | mask | tail -3
echo "dump readable exit: ${PIPESTATUS[0]}"

(cd "$OUT" && sha256sum supabase-full.dump supabase-public-schema.sql row-counts-supabase.txt > SHA256SUMS)
ls -l "$OUT"
echo "tables counted: $(wc -l < "$OUT/row-counts-supabase.txt"), total rows: $(awk -F'|' '{s+=$2} END {print s}' "$OUT/row-counts-supabase.txt")"
echo "public tables in dump: $(grep -c ' TABLE DATA public ' "$OUT/dump-contents.txt")"
