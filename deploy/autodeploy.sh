#!/usr/bin/env bash
#
# Pull-based auto-deploy. Runs from a systemd timer every few minutes: if
# GitHub has new commits on the tracked branch, pull them, rebuild, restart
# the stack and run migrations. Otherwise exit quietly.
#
# Pull-based rather than a webhook so nothing on GitHub needs a key to this
# server and no inbound port needs opening: the droplet already has a
# read-only deploy key, and that is all this needs.
#
#   bash deploy/install-autodeploy.sh      installs the timer (once)
#   journalctl -u tracker-deploy -n 50     what the last runs did
#   tail /var/log/tracker-deploy.log       same, as a plain file

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/tracker}"
LOG=/var/log/tracker-deploy.log

cd "$INSTALL_DIR"
exec 9>/var/lock/tracker-deploy.lock
# A build takes several minutes and the timer fires more often than that.
flock -n 9 || exit 0

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

git fetch --quiet origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse '@{u}')
[ "$LOCAL" != "$REMOTE" ] || exit 0

log "deploying $(git rev-parse --short "$REMOTE") (was $(git rev-parse --short "$LOCAL"))"
git pull --quiet --ff-only

# Rebuild everything the commit touched; unchanged services are cache hits
# and cost seconds. Bring the stack up before migrating so the API image
# that runs the migration is the new one.
docker compose build 2>&1 | tail -3 | tee -a "$LOG"
docker compose up -d 2>&1 | tail -3 | tee -a "$LOG"
docker compose exec -T api alembic upgrade head 2>&1 | tail -2 | tee -a "$LOG"

# Old image layers pile up at a few hundred MB per frontend build.
docker image prune -f >/dev/null 2>&1 || true

OK=$(curl -so /dev/null -w '%{http_code}' http://127.0.0.1:8000/healthz || echo 000)
log "done; API health $OK; now at $(git rev-parse --short HEAD)"
