#!/usr/bin/env bash
# upload_inventory.sh - READ-ONLY inventory of the upload volumes, compared with the database.
#
# For each environment it reports: file count, total size, oldest/newest modification time,
# database references whose file is missing, and files no database row refers to.
#
# READ-ONLY by construction:
#   - files are only listed (find -printf); nothing is deleted, moved, touched or truncated;
#   - the database is queried in a session forced read-only (default_transaction_read_only=on),
#     through the container's local socket - no password or connection string is read or printed;
#   - no temporary files are written, no network requests are made.
# It reports; it never decides that anything is safe to delete. Retention is INDEFINITE.
#
# Usage:  upload_inventory.sh [staging] [production]     (default: both)
# Env:    LIST_LIMIT (default 20)  rows listed per category
#         RECENT_MIN (default 60)  unreferenced files newer than this may be uploads in progress
# Needs:  sudo for docker and for reading /var/lib/docker/volumes (read access only).
set -uo pipefail

LIST_LIMIT="${LIST_LIMIT:-20}"
RECENT_MIN="${RECENT_MIN:-60}"
PREFIX="/app/uploads"          # UPLOAD_PATH inside the app container: what LocalStorageService stores
(( $# )) || set -- staging production

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "${1}B"; }

status=0
for env in "$@"; do
  case "$env" in
    staging)    project=invoice-staging ;;
    production) project=invoice-prod ;;
    *) echo "unknown environment: $env"; status=3; continue ;;
  esac
  vol="${project}_uploads"; db="${project}-db"
  echo "== $env  (volume $vol, database container $db)"

  mp=$(sudo -n docker volume inspect -f '{{.Mountpoint}}' "$vol" 2>/dev/null) \
    || { echo "  UNKNOWN: volume $vol not found"; status=3; continue; }

  # <mtime epoch>\t<bytes>\t<path relative to the volume root>
  files=$(sudo -n find "$mp" -xdev -type f -printf '%T@\t%s\t%P\n' 2>/dev/null) \
    || { echo "  UNKNOWN: cannot list $mp"; status=3; continue; }

  # Every file reference in the database (a document's own path and each page's path).
  refs=$(sudo -n docker exec -e PGOPTIONS='-c default_transaction_read_only=on' "$db" \
           sh -c 'psql -X -A -t -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
             select file_path from documents
             union
             select file_path from document_pages"' 2>&1) \
    || { echo "  UNKNOWN: read-only database query failed"; status=3; continue; }

  now=$(date +%s)
  awk -F'\t' -v prefix="$PREFIX/" -v now="$now" -v recent="$((RECENT_MIN * 60))" -v limit="$LIST_LIMIT" '
    function human(b,  u, i) { split("B KB MB GB TB", u, " "); i = 1; while (b >= 1024 && i < 5) { b /= 1024; i++ } return sprintf(i == 1 ? "%d %s" : "%.1f %s", b, u[i]) }
    function iso(t) { return strftime("%Y-%m-%dT%H:%M:%SZ", int(t), 1) }
    FNR == NR {                                  # first input: files on disk
      if ($3 == "") next
      n++; bytes += $2; mt[$3] = $1; sz[$3] = $2
      if (oldest == "" || $1 < oldest) oldest = $1
      if (newest == "" || $1 > newest) newest = $1
      next
    }
    $0 != "" {                                   # second input: database references
      total_refs++
      if (index($0, prefix) == 1)      { rel = substr($0, length(prefix) + 1); local_ref[rel] = 1; n_local++ }
      else if (substr($0, 1, 1) == "/") { n_other++; other[n_other] = $0 }
      else                              { n_external++ }   # bucket keys (historical Supabase uploads)
    }
    END {
      printf "  files on disk: %d, total %s\n", n, human(bytes)
      printf "  oldest modified: %s   newest modified: %s\n", (n ? iso(oldest) : "-"), (n ? iso(newest) : "-")
      printf "  database references: %d\n", total_refs
      printf "    in this volume (%s...): %d\n", prefix, n_local
      printf "    historical, kept outside this volume (Supabase bucket keys): %d  - expected, not counted as missing\n", n_external
      printf "    other absolute paths (unexpected): %d\n", n_other
      for (i = 1; i <= n_other && i <= limit; i++) printf "      %s\n", other[i]
      for (r in local_ref) if (!(r in mt)) missing[++n_missing] = r
      printf "  MISSING (referenced, no file on disk): %d\n", n_missing
      for (i = 1; i <= n_missing && i <= limit; i++) printf "      %s\n", missing[i]
      for (f in mt) if (!(f in local_ref)) { if (now - mt[f] < recent) n_recent++; else orphan[++n_orphan] = f }
      printf "  UNREFERENCED files (no database row): %d older than %d min, %d newer (may be uploads in progress)\n", n_orphan, recent / 60, n_recent
      for (i = 1; i <= n_orphan && i <= limit; i++) printf "      %s  %s  %s\n", iso(mt[orphan[i]]), human(sz[orphan[i]]), orphan[i]
      if (n_missing + n_orphan > limit * 2) printf "  (lists capped at %d rows each; set LIST_LIMIT to see more)\n", limit
      print "  Nothing was changed. Unreferenced files are NOT automatically safe to delete - see the runbook."
    }
  ' <(printf '%s\n' "$files") <(printf '%s\n' "$refs")
done
exit "$status"
