# OCI shared VM — uploads, disk and recovery runbook

Applies to Invoice Intelligence Engine on the shared OCI VM (Ubuntu 24.04 ARM64, 2 cores, 11 GB RAM, one 96 GB disk).
Production: `/opt/edihor/production` (Compose project `invoice-prod`, app 127.0.0.1:8100).
Staging: `/opt/edihor/staging` (Compose project `invoice-staging`, app 127.0.0.1:8101).
Another project shares this VM (systemd Java services, host PostgreSQL 16, host Nginx). It must never be disturbed.

Scripts referenced here live in [`scripts/ops/`](../scripts/ops/). On the VM they are run from a checkout, or streamed over SSH without copying anything:
`ssh ubuntu@<vm> 'bash -s' < scripts/ops/disk_check.sh`.
Both are read-only.

The environment set-up and data-load helpers used during the migration live on the VM, not in this repository:
- `/opt/edihor/setup_env.sh`
- `/opt/edihor/migration/restore_data.sh`

## 1. Upload retention: indefinite

- Every uploaded invoice photo or PDF is kept **indefinitely**.
- There is **no** automatic, age-based or size-based deletion, and none may be added without an owner decision.
- Files are removed **only** through the application's authorised manual workflows:
  - discarding an upload before it is processed;
  - ADMIN purge from the Recycle Bin (directly, or by approving a deletion request).
- Never delete, move or rename files inside an upload volume by hand. The database refers to each file by path. A file removed by hand becomes a "missing" reference, and Reprocess fails for that invoice.
- An "unreferenced" file in `upload_inventory.sh` output is **not** automatically safe to delete. Possible causes:
  - an upload still in progress (the file is written before its database row);
  - a failed upload;
  - a Recycle Bin purge whose file cleanup failed (logged as `bin_purge_file_cleanup_failed`).
  Investigate each one; delete only with owner approval.
- The 20 historical invoices migrated from Supabase keep their original Supabase file keys. Their files were deliberately **not** copied: Reprocess is unavailable for them, and the inventory reports them as "historical", not missing. Do not modify these references. The original invoice is reached through `invoices.reference_url` (the original invoice / chat link).

## 2. Where the data lives (Docker volume persistence)

| Data | Volume | Path on the VM |
|---|---|---|
| Production uploads | `invoice-prod_uploads` | `/var/lib/docker/volumes/invoice-prod_uploads/_data` |
| Production database | `invoice-prod_pgdata` | `/var/lib/docker/volumes/invoice-prod_pgdata/_data` |
| Staging uploads | `invoice-staging_uploads` | `/var/lib/docker/volumes/invoice-staging_uploads/_data` |
| Staging database | `invoice-staging_pgdata` | `/var/lib/docker/volumes/invoice-staging_pgdata/_data` |

All four volumes are on the VM's single boot disk, shared with the other project.

- **Survive:** `docker compose restart`, `stop`/`start`, `up -d` (including recreating containers for a new image or env change), `down` **without** `-v`, Docker upgrades, VM reboots.
- **Destroyed by:** `docker compose down -v`, `docker volume rm`, `docker volume prune`, `docker system prune --volumes`, terminating the VM or deleting its boot volume, disk failure.

## 3. Prohibited commands on this VM

| Never run | Why |
|---|---|
| `docker compose down -v` (in either project) | Deletes that environment's database **and** uploads |
| `docker volume prune` / `docker system prune --volumes` | Deletes every volume not attached to a running container, e.g. while an environment is stopped |
| `docker volume rm invoice-*` | Deletes data directly |
| `rm`, `mv`, `find -delete` under `/var/lib/docker/volumes` | Bypasses the application; breaks references |
| Any cleanup in `/opt/hor`, host PostgreSQL 16, or the other project's Nginx sites | Not ours |

To stop an environment safely: `cd /opt/edihor/<env> && sudo docker compose stop` (or `down`, **without** `-v`).
Clearing Docker build cache (`docker builder prune`) does not touch volumes, but run it only with approval, never combined with `--volumes`.

## 4. Disk exhaustion on a shared VM

When the disk fills up:
- uploads fail with HTTP 503 `ERR_STORAGE_UNAVAILABLE`, and nothing is saved;
- both PostgreSQL containers stop accepting writes or crash;
- **the other project's** PostgreSQL 16, Java services and Nginx logging fail too.

Running out of disk is therefore a shared outage, not just our problem.

**Monitoring:** `disk_check.sh` checks the filesystem holding `/opt/edihor` and `/var/lib/docker`:
- **WARNING at 80%** (exit code 1);
- **CRITICAL at 90%** (exit code 2);
- inodes are checked against the same thresholds.

**Alerts:** once installed and enabled, `edihor-disk-check.timer` runs the check hourly and emails through Healthchecks.io. It also alerts when the check stops reporting. See section 8.

**At WARNING (80%):**
1. Run `disk_check.sh` and `upload_inventory.sh`.
2. Look at `sudo docker system df` and `sudo du -sh /var/lib/docker/volumes/*`.
3. Report to the owner and plan capacity: grow the boot volume in the OCI console, or attach a block volume. Do **not** delete uploads.

**At CRITICAL (90%):**
1. Notify the owner and the other project's owner.
2. Only approved, non-data actions:
   - `docker builder prune` (build cache only);
   - remove old **unused** app images (`docker image rm invoice-engine:<old tag>`, never the one running);
   - `journalctl --vacuum-size=…`.
3. Then expand the disk. Never delete uploads or database data to make room.

**Already capped:**
- Docker container logs: 10 MB × 3 files per container (`/etc/docker/daemon.json`).
- journald: Ubuntu's default size limit (about 284 MB in use on 12 Nov 2026).

## 5. Backups: database and uploads are separate

| What | How | Covers | Does not cover |
|---|---|---|---|
| Database (per environment) | `pg_dump -Fc` from the db container, copied **off the VM** | All invoices, items, mappings, users, EDI exports (export file content is stored in the database), reference links | Upload files |
| Upload files (per environment) | Separate copy of the uploads volume (e.g. tar or rsync), copied **off the VM** | Original photos and PDFs | Database |
| Whole disk (optional) | OCI boot-volume backup policy | Everything on the VM, **including the other project** | Must be agreed with the other project's owner |

- A database backup alone restores every invoice, but not the original files: Reprocess and the stored originals would be lost. An uploads backup alone is useless without the database.
- The two backups are taken at different moments, so a restore can show a few missing or unreferenced files. Run `upload_inventory.sh` after every restore to list them.
- Status today: **no automated backups exist** for either environment. The only backup is the one-off Supabase snapshot of 12 Nov 2026 (`/opt/edihor/migration/backup-20261112T103234Z`, copy on the owner's Mac).

## 6. Recovery

**App container broken:** `cd /opt/edihor/<env> && sudo docker compose up -d --force-recreate app`. Volumes are untouched.

**Bad deploy:** set `APP_IMAGE=invoice-engine:<previous tag>` in the shell and run `docker compose up -d app`. Data is untouched, but a migration that already ran is not undone.

**Database lost or corrupted** (volume intact or recreated):
1. Stop the app.
2. Restore the latest `pg_dump` into the db container (`restore_data.sh` replaces data in one transaction; for a full restore with schema, use `pg_restore` into an empty database).
3. Start the app, then run `upload_inventory.sh`.

**Uploads volume lost:**
1. Restore from the uploads backup into the (recreated) volume, keeping the same relative paths.
2. Run `upload_inventory.sh`.
3. Invoices whose files cannot be recovered keep working, but Reprocess is unavailable for them. Their originals remain reachable through their reference links.

**Whole VM lost:**
1. New VM, then Docker.
2. `setup_env.sh` (new secrets; API keys re-sent).
3. Restore the database, then the uploads.
4. Re-point Nginx and DNS.

Without off-VM backups, this scenario loses all data since the last copy.

## 7. Routine checks

- `disk_check.sh`: hourly by `edihor-disk-check.timer` once it is enabled (section 8). Until then, run it by hand daily.
- `upload_inventory.sh`: weekly, after any restore, and before any change to storage.
- Before any maintenance, confirm the other project is healthy: `systemctl is-active hor-backend-dash chat-backend postgresql@16-main nginx`, and both sites return 200.

## 8. Disk monitoring and alerting

**Status:** the files are in this repository. Installing them on the VM and creating the Healthchecks.io check each need the owner's explicit approval. Until then, nothing in this section is active.

### How it works

1. `edihor-disk-check.timer` starts `edihor-disk-check.service`:
   - every hour, at a random minute within 5 minutes;
   - once after boot if a run was missed.
2. The service runs `scripts/ops/disk_alert.sh`, which runs `scripts/ops/disk_check.sh`:
   - **WARNING at 80%**, **CRITICAL at 90%** (disk space or inodes), for the single filesystem holding `/opt/edihor` and `/var/lib/docker`;
   - this disk also holds the other project's data, so the check protects both.
3. The full report goes to the journal: `journalctl -t edihor-disk`.
4. One HTTPS ping goes to Healthchecks.io: `https://hc-ping.com/<uuid>/<exit code>`.
   - The exit code is the state: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
   - The body is a single line, for example `state=WARNING used=82%`.
   - Nothing else is sent: no hostname, paths, sizes or application data. Healthchecks.io can see the VM's IP address.
5. Healthchecks.io (one check, period **1 hour**, grace **2 hours**) emails the recipients:
   - **once** when the state becomes WARNING, CRITICAL or UNKNOWN;
   - when **no ping arrives** within period + grace (VM down, timer stopped, network broken);
   - when the check recovers.

   Optional hourly or daily reminder emails can be turned on in the account settings while a check is down.

### Isolation

The service runs as a throwaway user (`DynamicUser=yes`):
- no `sudo`, no Docker access, no capabilities, no group memberships;
- the whole filesystem read-only, a private `/tmp`, and limited system calls;
- `Nice=19`, idle disk priority, 10% CPU, 64 MB memory, 90-second timeout.

It can read disk statistics and make one outbound HTTPS request. It cannot change application data, containers, Docker or the other project.

### Files

| In the repository | On the VM | Owner / mode |
|---|---|---|
| `scripts/ops/disk_check.sh`, `scripts/ops/disk_alert.sh` | `/opt/edihor/ops/` | root:root 0755 |
| `deploy/systemd/edihor-disk-check.service`, `.timer` | `/etc/systemd/system/` | root:root 0644 |
| `deploy/systemd/disk-alert.env.example` (placeholder only) | `/etc/edihor/disk-alert.env` (the real ping URL) | root:root 0600, directory 0700 |

The ping URL works like a password. It is never committed, pasted into chat or printed. `disk_alert.sh` refuses any value that is not `https://hc-ping.com/<uuid>`.

### One-time setup (after approval)

**Healthchecks.io:**
1. Create the account with a team email address.
2. Create one check named `oci-vm-disk`: Simple schedule, period 1 hour, grace 2 hours.
3. Add the email recipients. Consider the other project's owner too.
4. Copy the ping URL; it is sent to the VM in the next steps.

**On the VM**, from a checkout of the approved commit (`<repo>` below):
```
sudo install -d -m 0755 -o root -g root /opt/edihor/ops
sudo install -m 0755 -o root -g root <repo>/scripts/ops/disk_check.sh <repo>/scripts/ops/disk_alert.sh /opt/edihor/ops/
sudo install -d -m 0700 -o root -g root /etc/edihor
# The ping URL is written to /etc/edihor/disk-alert.env (mode 0600) from the owner's clipboard over SSH, never typed or echoed.
sudo install -m 0644 -o root -g root <repo>/deploy/systemd/edihor-disk-check.service <repo>/deploy/systemd/edihor-disk-check.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/edihor-disk-check.service /etc/systemd/system/edihor-disk-check.timer
sudo systemctl daemon-reload
sudo systemctl start edihor-disk-check.service      # one run now (test 1)
sudo systemctl enable --now edihor-disk-check.timer
```
Before and after: check that the other project is healthy (section 7).

### Test cases

Forced thresholds use a one-off unit that runs as a throwaway user with a read-only filesystem, like the service. They never change the installed files.
```
sudo systemd-run --wait --pipe --quiet -p DynamicUser=yes -p ProtectSystem=strict -p EnvironmentFile=/etc/edihor/disk-alert.env -E WARN_PCT=<w> -E CRIT_PCT=<c> /opt/edihor/ops/disk_alert.sh
```

| # | Test | How | Expected |
|---|---|---|---|
| 1 | Normal run | `systemctl start edihor-disk-check.service` | Journal shows `OK`. Healthchecks.io shows the check up, last ping exit 0. No email. |
| 2 | Warning | one-off unit, `WARN_PCT=10 CRIT_PCT=90` | Exit 1 ping. One "down" email with body `state=WARNING used=N%`. |
| 3 | Critical | one-off unit, `WARN_PCT=5 CRIT_PCT=10` | Exit 2 ping, body `state=CRITICAL ...`. No second email: the check is already down. |
| 4 | Recovery | `systemctl start edihor-disk-check.service` | Exit 0 ping. One "up" (recovery) email. |
| 5 | Missed pings | Temporarily set the check's period and grace to 1 minute in the dashboard; wait 3 minutes | "down" email for a late check. Restore 1 hour / 2 hours, then run test 4 to bring it back up. |
| 6 | Broken alert path | the same one-off command **without** `-p EnvironmentFile=...` (the file would override `-E`) and with `-E HC_PING_URL=https://hc-ping.com/not-a-uuid` | Unit fails. Journal says the URL is not of the expected form. Nothing sent, and the URL is not printed. |
| 7 | Sandbox | `systemd-analyze security edihor-disk-check.service`, and `systemd-run --wait --pipe -p DynamicUser=yes -p ProtectSystem=strict touch /opt/edihor/sandbox-test` | Low exposure score. The `touch` fails with "Read-only file system". |
| 8 | Other project | `systemctl is-active hor-backend-dash chat-backend postgresql@16-main nginx`, both sites, before and after | Unchanged: same PIDs, both sites 200. |

### Operating notes

- **Planned maintenance** (VM reboot, network work): pause the check in the Healthchecks.io dashboard, then resume it afterwards.
- **Logs:** `journalctl -t edihor-disk --since today`.
- **Timer status:** `systemctl list-timers edihor-disk-check.timer`.
- **Changing thresholds:** only by an approved change to this repository. The defaults stay 80% / 90%.

### Rollback (about 2 minutes, no effect on the application)

```
sudo systemctl disable --now edihor-disk-check.timer
sudo rm /etc/systemd/system/edihor-disk-check.service /etc/systemd/system/edihor-disk-check.timer
sudo systemctl daemon-reload
sudo shred -u /etc/edihor/disk-alert.env && sudo rmdir /etc/edihor
sudo rm /opt/edihor/ops/disk_alert.sh   # disk_check.sh may stay for manual runs
```
Then pause or delete the check in Healthchecks.io so it stops expecting pings.
