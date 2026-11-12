#!/usr/bin/env bash
# disk_alert.sh - run disk_check.sh and report the result to one Healthchecks.io check.
#
# The full disk report goes to stdout only: the system journal when run by systemd
# (tag edihor-disk). Healthchecks.io receives ONLY:
#   - the state as the ping's exit code: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN
#     (any non-zero code marks the check "down" and sends one email);
#   - a one-line body: "state=<STATE> used=<highest disk use>%".
# No hostname, paths, sizes, project names or application data leave the VM.
# Healthchecks.io also alerts when pings stop arriving (VM down, timer broken, no network).
#
# Env:  HC_PING_URL         https://hc-ping.com/<uuid>. A secret: only in /etc/edihor/disk-alert.env,
#                           never in Git, never printed.
#       WARN_PCT, CRIT_PCT  passed through to disk_check.sh (defaults 80 / 90).
#       DISK_CHECK          path of disk_check.sh (default: next to this script).
# Exit: 0 when the state was reported (whatever the state); 1 when nothing could be sent,
#       so `systemctl --failed` shows a broken alert path.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
check="${DISK_CHECK:-$here/disk_check.sh}"

report=$("$check")
code=$?
printf '%s\n' "$report"

case $code in
  0) state=OK ;;
  1) state=WARNING ;;
  2) state=CRITICAL ;;
  *) state=UNKNOWN; code=3 ;;
esac
# Highest disk use among the reported filesystems ("...): 82% used - ..."), inodes excluded.
used=$(printf '%s\n' "$report" | grep -oE '\): [0-9]+% used' | grep -oE '[0-9]+' | sort -n | tail -1)
body="state=$state used=${used:-?}%"

url="${HC_PING_URL:-}"
uuid_re='^https://hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
if [[ -z "$url" ]]; then
  echo "alert: HC_PING_URL is not set - nothing sent"
  exit 1
fi
if ! [[ "$url" =~ $uuid_re ]]; then
  echo "alert: HC_PING_URL is not of the form https://hc-ping.com/<uuid> - nothing sent"
  exit 1
fi

# curl's own error text is discarded because it can contain the URL.
if curl -fsS -o /dev/null --max-time 10 --retry 3 --retry-delay 5 \
     -A "edihor-disk-alert" --data-raw "$body" "$url/$code" 2>/dev/null; then
  echo "alert: reported $body (exit code $code)"
else
  echo "alert: ping FAILED for $body (exit code $code); Healthchecks.io will alert if pings keep failing"
  exit 1
fi
