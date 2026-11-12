#!/usr/bin/env bash
# disk_check.sh - report disk usage of the filesystem holding /opt/edihor (and Docker's data
# directory, if that is a different filesystem). READ-ONLY: no writes, no network, no installs,
# no configuration changes. Needs no root (Docker's data path is only checked if readable).
#
# Usage:   disk_check.sh [path ...]        default: /opt/edihor /var/lib/docker
# Env:     WARN_PCT (default 80)  CRIT_PCT (default 90)
# Exit:    0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN   (the usual monitoring-plugin convention,
#          so any later alert channel can act on the exit code alone)
set -u

WARN_PCT="${WARN_PCT:-80}"
CRIT_PCT="${CRIT_PCT:-90}"
if ! [[ "$WARN_PCT" =~ ^[0-9]+$ && "$CRIT_PCT" =~ ^[0-9]+$ ]] || (( WARN_PCT >= CRIT_PCT || CRIT_PCT > 100 )); then
  echo "UNKNOWN - invalid thresholds WARN_PCT=$WARN_PCT CRIT_PCT=$CRIT_PCT"; exit 3
fi
(( $# )) || set -- /opt/edihor /var/lib/docker

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "${1}B"; }

# Overall state: CRITICAL outranks UNKNOWN, which outranks WARNING, which outranks OK.
rank() { case $1 in 2) echo 3 ;; 3) echo 2 ;; *) echo "$1" ;; esac; }
worst=0; seen=""; lines=()
bump() { (( $(rank "$1") > $(rank "$worst") )) && worst=$1; }
for path in "$@"; do
  [[ -e "$path" ]] || { lines+=("UNKNOWN  $path does not exist"); bump 3; continue; }
  # One filesystem is reported once, however many paths live on it.
  read -r dev size used avail pcent mount < <(df -P -B1 "$path" 2>/dev/null | awk 'NR==2 {print $1, $2, $3, $4, $5, $6}')
  [[ -n "${dev:-}" ]] || { lines+=("UNKNOWN  df failed for $path"); bump 3; continue; }
  [[ " $seen " == *" $dev "* ]] && continue
  seen="$seen $dev"
  pct="${pcent%\%}"                                   # df's own figure: used / (used + available)
  read -r ipct < <(df -P -i "$path" | awk 'NR==2 {gsub("%", "", $5); print $5}')
  if   (( pct >= CRIT_PCT )) || [[ "${ipct:-0}" =~ ^[0-9]+$ && ipct -ge CRIT_PCT ]]; then state=CRITICAL; code=2
  elif (( pct >= WARN_PCT )) || [[ "${ipct:-0}" =~ ^[0-9]+$ && ipct -ge WARN_PCT ]]; then state=WARNING;  code=1
  else state=OK; code=0; fi
  bump "$code"
  lines+=("$(printf '%-8s %s on %s (%s): %s%% used - %s used of %s, %s free; inodes %s%% used' \
    "$state" "$path" "$mount" "$dev" "$pct" "$(human "$used")" "$(human "$size")" "$(human "$avail")" "${ipct:-?}")")
done

case $worst in 0) s=OK ;; 1) s=WARNING ;; 2) s=CRITICAL ;; *) s=UNKNOWN ;; esac
echo "$s - disk check $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname) (warn ${WARN_PCT}%, critical ${CRIT_PCT}%)"
printf '  %s\n' "${lines[@]}"
exit "$worst"
