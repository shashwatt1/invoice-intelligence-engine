#!/usr/bin/env bash
# deploy.sh - deploy one Git commit to one environment on the OCI VM.
#
#   /opt/edihor/ops/deploy.sh staging <commit>
#   /opt/edihor/ops/deploy.sh production <commit>
#
# Steps, in order (any failure stops the deploy):
#   1. fetch the commit from GitHub (origin) into the VM checkout and check it out
#   2. build the app image tagged with the commit (reused if that tag already exists)
#   3. take a pre-deploy database backup (pg_dump, custom format, verified readable)
#   4. run database migrations with the NEW image (alembic upgrade head)
#   5. switch the app to the new image; the previous image is recorded for rollback
#   6. health check; if it fails, the app is switched back to the previous image
#
# Guards:
#   * production only deploys commits that are on origin/main AND were deployed to
#     staging successfully first (override: ALLOW_PRODUCTION_WITHOUT_STAGING=1)
#   * one deploy at a time (lock); the VM checkout must have no local changes
#   * migrations are never reverted automatically; the pre-deploy backup is kept
#
# Run it from the installed copy in /opt/edihor/ops, never from the checkout:
# step 1 changes the checkout, which would change this script while it runs.
# Rollback: /opt/edihor/ops/rollback.sh <env>. See docs/oci-vm-runbook.md, section 9.
set -euo pipefail

ENV_NAME="${1:-}"
COMMIT="${2:-}"
REPO="${EDIHOR_REPO:-/opt/edihor/invoice-intelligence-engine}"
BASE=/opt/edihor
case "$ENV_NAME" in
  staging)    PROJECT=invoice-staging; PORT=8101 ;;
  production) PROJECT=invoice-prod;    PORT=8100 ;;
  *) echo "usage: deploy.sh staging|production <commit>" >&2; exit 2 ;;
esac
[[ -n "$COMMIT" ]] || { echo "usage: deploy.sh $ENV_NAME <commit>" >&2; exit 2; }
DIR="$BASE/$ENV_NAME"
APP_CONTAINER="$PROJECT-app"
STATE="$DIR/.env"                 # read by docker compose: APP_IMAGE / PREVIOUS_IMAGE
LOG="$DIR/deploy.log"
BACKUPS="$BASE/backups/$ENV_NAME"
TS=$(date -u +%Y%m%dT%H%M%SZ)

say()  { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { say "FAILED: $*"; log_line FAILED "${SHA:-$COMMIT}"; exit 1; }
log_line() { printf '%s %s deploy %s %s from %s by %s\n' "$(date -u +%FT%TZ)" "$1" "$ENV_NAME" "$2" "${PREV_IMAGE:-?}" "$(id -un)" >> "$LOG"; }
compose() { (cd "$DIR" && sudo -n docker compose "$@"); }
healthy() {
  for _ in $(seq 1 24); do
    if curl -fsS -m 10 "http://127.0.0.1:$PORT/api/v1/health" 2>/dev/null | grep -q '"database":"connected"'; then return 0; fi
    sleep 5
  done
  return 1
}

exec 9>"$BASE/.deploy.lock"
flock -n 9 || { echo "another deploy or rollback is running" >&2; exit 1; }

say "== deploy $ENV_NAME <- $COMMIT"

# 1. code
[[ -z "$(git -C "$REPO" status --porcelain)" ]] || fail "the VM checkout $REPO has local changes"
git -C "$REPO" fetch --quiet --prune origin
SHA=$(git -C "$REPO" rev-parse --verify --quiet "$COMMIT^{commit}" || git -C "$REPO" rev-parse --verify --quiet "origin/$COMMIT^{commit}") \
  || fail "commit $COMMIT not found on origin"
SHORT=${SHA:0:7}
if [[ "$ENV_NAME" == production ]]; then
  git -C "$REPO" merge-base --is-ancestor "$SHA" origin/main || fail "$SHORT is not on origin/main"
  if [[ "${ALLOW_PRODUCTION_WITHOUT_STAGING:-0}" != 1 ]] && ! grep -q " OK deploy staging $SHA " "$BASE/staging/deploy.log" 2>/dev/null; then
    fail "$SHORT has not been deployed to staging successfully (set ALLOW_PRODUCTION_WITHOUT_STAGING=1 to override)"
  fi
fi
git -C "$REPO" -c advice.detachedHead=false checkout --quiet --detach "$SHA"
say "1. code at $SHORT: $(git -C "$REPO" log -1 --format=%s)"

# current image -> a stable tag to roll back to
PREV_IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$STATE" 2>/dev/null || true)
if [[ -z "$PREV_IMAGE" ]]; then
  PREV_ID=$(sudo -n docker inspect -f '{{.Image}}' "$APP_CONTAINER")
  PREV_IMAGE=$(sudo -n docker image inspect -f '{{range .RepoTags}}{{println .}}{{end}}' "$PREV_ID" | grep -E '^invoice-engine:[0-9a-f]{7,40}$' | head -1 || true)
  if [[ -z "$PREV_IMAGE" ]]; then PREV_IMAGE="invoice-engine:rollback-$TS"; sudo -n docker tag "$PREV_ID" "$PREV_IMAGE"; fi
fi
NEW_IMAGE="invoice-engine:$SHORT"
[[ "$NEW_IMAGE" != "$PREV_IMAGE" ]] || { say "$ENV_NAME already runs $NEW_IMAGE - nothing to do"; exit 0; }

# 2. image
if sudo -n docker image inspect "$NEW_IMAGE" >/dev/null 2>&1; then
  say "2. image $NEW_IMAGE already built - reused"
else
  say "2. building $NEW_IMAGE (a few minutes)"
  sudo -n docker build --target runtime -t "$NEW_IMAGE" "$REPO" > "$DIR/last-build.log" 2>&1 || fail "image build (see $DIR/last-build.log)"
  say "   built $NEW_IMAGE"
fi

# 3. pre-deploy database backup
mkdir -p "$BACKUPS"; chmod 700 "$BASE/backups" "$BACKUPS"
DUMP="$BACKUPS/predeploy-$TS-$SHORT.dump"
( umask 077; compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DUMP" ) || fail "pre-deploy backup"
compose exec -T db pg_restore --list < "$DUMP" >/dev/null || fail "pre-deploy backup is not readable"
say "3. database backup $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1)) verified"

# 4. migrations with the new image
BEFORE=$(compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAtc "select version_num from alembic_version"')
(cd "$DIR" && sudo -n env APP_IMAGE="$NEW_IMAGE" docker compose run --rm --no-deps app alembic upgrade head > "$DIR/last-migrate.log" 2>&1) \
  || fail "migrations (see $DIR/last-migrate.log; backup: $DUMP)"
AFTER=$(compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAtc "select version_num from alembic_version"')
say "4. migrations: $BEFORE -> $AFTER"

# 5. switch
write_state() { printf 'APP_IMAGE=%s\nPREVIOUS_IMAGE=%s\n' "$1" "$2" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"; }
write_state "$NEW_IMAGE" "$PREV_IMAGE"
compose up -d --wait --wait-timeout 120 app >/dev/null 2>&1 || true
RUNNING=$(sudo -n docker inspect -f '{{.Config.Image}}' "$APP_CONTAINER")

# 6. health, or switch back
if [[ "$RUNNING" == "$NEW_IMAGE" ]] && healthy; then
  say "5. app switched: $PREV_IMAGE -> $NEW_IMAGE"
  say "6. health OK (http://127.0.0.1:$PORT/api/v1/health)"
  log_line OK "$SHA"
  say "== deployed $SHORT to $ENV_NAME. Rollback: /opt/edihor/ops/rollback.sh $ENV_NAME"
else
  say "6. health check FAILED on $NEW_IMAGE - switching back to $PREV_IMAGE"
  write_state "$PREV_IMAGE" "$NEW_IMAGE"
  compose up -d --wait --wait-timeout 120 app >/dev/null 2>&1 || true
  if healthy; then say "   back on $PREV_IMAGE and healthy"; else say "   WARNING: $PREV_IMAGE is not healthy either - investigate now"; fi
  [[ "$BEFORE" == "$AFTER" ]] || say "   NOTE: migrations ran ($BEFORE -> $AFTER) and were NOT reverted; backup: $DUMP"
  log_line ROLLED_BACK "$SHA"
  exit 1
fi
