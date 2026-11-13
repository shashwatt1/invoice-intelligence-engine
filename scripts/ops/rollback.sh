#!/usr/bin/env bash
# rollback.sh - switch one environment back to the image it ran before the last deploy.
#
#   /opt/edihor/ops/rollback.sh staging
#   /opt/edihor/ops/rollback.sh production
#
# Swaps APP_IMAGE and PREVIOUS_IMAGE in /opt/edihor/<env>/.env and restarts only the app
# container (seconds, no rebuild). Running it twice switches forward again.
# It also puts the VM checkout back on the commit of the image it switches to.
#
# The database is NOT changed. This project's migrations are additive, so the previous
# image keeps working on a newer schema. If the rolled-back deploy must also be undone in
# the database, restore that deploy's pre-deploy backup from /opt/edihor/backups/<env>/ -
# a separate, deliberate step that loses changes made since the deploy (runbook section 9).
set -euo pipefail

ENV_NAME="${1:-}"
REPO="${EDIHOR_REPO:-/opt/edihor/invoice-intelligence-engine}"
BASE=/opt/edihor
case "$ENV_NAME" in
  staging)    PROJECT=invoice-staging; PORT=8101 ;;
  production) PROJECT=invoice-prod;    PORT=8100 ;;
  *) echo "usage: rollback.sh staging|production" >&2; exit 2 ;;
esac
DIR="$BASE/$ENV_NAME"
STATE="$DIR/.env"
LOG="$DIR/deploy.log"
say() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
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

CUR=$(sed -n 's/^APP_IMAGE=//p' "$STATE" 2>/dev/null || true)
PREV=$(sed -n 's/^PREVIOUS_IMAGE=//p' "$STATE" 2>/dev/null || true)
[[ -n "$CUR" && -n "$PREV" ]] || { say "no recorded deploy in $STATE - nothing to roll back to"; exit 1; }
sudo -n docker image inspect "$PREV" >/dev/null 2>&1 || { say "previous image $PREV no longer exists - cannot roll back"; exit 1; }

say "== rollback $ENV_NAME: $CUR -> $PREV"
printf 'APP_IMAGE=%s\nPREVIOUS_IMAGE=%s\n' "$PREV" "$CUR" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
compose up -d --wait --wait-timeout 120 app >/dev/null 2>&1 || true
RUNNING=$(sudo -n docker inspect -f '{{.Config.Image}}' "$PROJECT-app")

if [[ "$RUNNING" == "$PREV" ]] && healthy; then
  say "app on $PREV and healthy"
  RESULT=OK
else
  say "WARNING: $PREV did not come up healthy (running: $RUNNING) - run rollback.sh again to return to $CUR"
  RESULT=FAILED
fi

TAG=${PREV#invoice-engine:}
if [[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]] && [[ -z "$(git -C "$REPO" status --porcelain)" ]] \
   && git -C "$REPO" -c advice.detachedHead=false checkout --quiet --detach "$TAG" 2>/dev/null; then
  say "VM checkout back on $TAG"
else
  say "VM checkout left as is (image tag $TAG is not a commit, or the checkout has changes)"
fi
VER=$(compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAtc "select version_num from alembic_version"')
say "database unchanged (migration $VER); pre-deploy backups: $BASE/backups/$ENV_NAME/"
printf '%s %s rollback %s %s from %s by %s\n' "$(date -u +%FT%TZ)" "$RESULT" "$ENV_NAME" "$PREV" "$CUR" "$(id -un)" >> "$LOG"
[[ "$RESULT" == OK ]]
