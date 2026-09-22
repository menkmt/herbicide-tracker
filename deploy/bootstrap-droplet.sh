#!/usr/bin/env bash
#
# One-shot setup for a fresh Ubuntu 24.04 droplet.
#
# Public repository:
#
#   curl -fsSL https://raw.githubusercontent.com/menkmt/herbicide-tracker/HEAD/deploy/bootstrap-droplet.sh | bash
#
# Private repository — the curl above cannot reach it, so give the droplet a
# read-only deploy key and clone first (see docs/DEPLOYMENT.md):
#
#   ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519
#   cat /root/.ssh/id_ed25519.pub      # add to GitHub as a read-only deploy key
#   ssh -T git@github.com              # accept the host key
#   git clone git@github.com:menkmt/herbicide-tracker.git /opt/tracker
#   bash /opt/tracker/deploy/bootstrap-droplet.sh
#
# It is safe to run twice: every step checks whether it has already been done.
#
# What it does NOT do: obtain a TLS certificate or open the site to the
# internet. It leaves the stack listening on localhost only, so you can check
# it works before anything is publicly reachable. See docs/DEPLOYMENT.md for
# the reverse proxy step.

set -euo pipefail

# Defaults to SSH when the droplet has a key that GitHub accepts, so a private
# repository works without editing anything. Override REPO_URL to force one.
REPO_SSH="git@github.com:menkmt/herbicide-tracker.git"
REPO_HTTPS="https://github.com/menkmt/herbicide-tracker.git"
REPO_URL="${REPO_URL:-}"
# Empty means the repository default branch, which is where the work lives.
REPO_REF="${REPO_REF:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tracker}"

log()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mXX\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root (or under sudo)"

# ---------------------------------------------------------------------------
log "Checking the machine is big enough"
# ---------------------------------------------------------------------------
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
CPUS=$(nproc)
DISK_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')

printf '    %s MB RAM, %s vCPU, %s GB free disk\n' "$MEM_MB" "$CPUS" "$DISK_GB"
# OCR is the constraint: tesseract on a 300dpi page wants a few hundred MB and
# a whole core, on top of Postgres, Redis, the API and the Next.js server.
[ "$MEM_MB" -ge 3500 ] || warn "under 4 GB RAM — OCR of large scanned permits may be killed"
[ "$DISK_GB" -ge 20 ]  || warn "under 20 GB free — fine for text, tight if you store scans locally"

# ---------------------------------------------------------------------------
log "Installing Docker"
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    printf '    already installed: %s\n' "$(docker --version)"
else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl git gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# ---------------------------------------------------------------------------
log "Configuring the firewall"
# ---------------------------------------------------------------------------
# Nothing is exposed yet. 80/443 are opened ready for the reverse proxy; the
# app's own ports stay closed and are reached through it.
apt-get install -y -qq ufw >/dev/null 2>&1 || true
ufw --force default deny incoming >/dev/null
ufw --force default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80,443/tcp >/dev/null
ufw --force enable >/dev/null
printf '    SSH, 80 and 443 open; 8000 and 3000 closed\n'

# ---------------------------------------------------------------------------
log "Fetching the code"
# ---------------------------------------------------------------------------
if [ -z "$REPO_URL" ]; then
    # `ssh -T git@github.com` exits 1 on success ("successfully authenticated,
    # but GitHub does not provide shell access"), so match on the message
    # rather than the exit code.
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -T \
           git@github.com 2>&1 | grep -q "successfully authenticated"; then
        REPO_URL="$REPO_SSH"
        printf '    using SSH (deploy key accepted)\n'
    else
        REPO_URL="$REPO_HTTPS"
        printf '    using HTTPS (no deploy key; fine for a public repository)\n'
    fi
fi

if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch --quiet origin
    if [ -n "$REPO_REF" ]; then
        git -C "$INSTALL_DIR" checkout --quiet "$REPO_REF"
    fi
    git -C "$INSTALL_DIR" pull --quiet
elif [ -n "$REPO_REF" ]; then
    git clone --quiet --branch "$REPO_REF" "$REPO_URL" "$INSTALL_DIR"
else
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ---------------------------------------------------------------------------
log "Generating secrets"
# ---------------------------------------------------------------------------
if [ -f .env ]; then
    printf '    .env already exists — leaving it alone\n'
else
    cp .env.example .env
    # Generated rather than prompted, so nobody is tempted to pick something
    # memorable for the admin token.
    ADMIN_TOKEN=$(openssl rand -hex 32)
    sed -i "s|^TRACKER_ENVIRONMENT=.*|TRACKER_ENVIRONMENT=production|" .env
    sed -i "s|^TRACKER_SECRET_KEY=.*|TRACKER_SECRET_KEY=$(openssl rand -hex 32)|" .env
    sed -i "s|^TRACKER_ADMIN_TOKEN=.*|TRACKER_ADMIN_TOKEN=${ADMIN_TOKEN}|" .env
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
    chmod 600 .env
    printf '    written to .env (mode 600)\n'
fi

# ---------------------------------------------------------------------------
log "Building and starting the stack"
# ---------------------------------------------------------------------------
docker compose build
docker compose up -d
printf '    waiting for the database'
for _ in $(seq 1 60); do
    if docker compose exec -T db pg_isready -U tracker >/dev/null 2>&1; then break; fi
    printf '.'; sleep 2
done
printf '\n'
docker compose exec -T api alembic upgrade head

# ---------------------------------------------------------------------------
log "Checking it came up"
# ---------------------------------------------------------------------------
sleep 5
API_OK=$(curl -so /dev/null -w '%{http_code}' http://127.0.0.1:8000/healthz || echo 000)
WEB_OK=$(curl -so /dev/null -w '%{http_code}' http://127.0.0.1:3000/ || echo 000)
printf '    API  %s\n    site %s\n' "$API_OK" "$WEB_OK"
[ "$API_OK" = "200" ] || die "the API did not come up — check: docker compose logs api"

# ---------------------------------------------------------------------------
log "Probing the GIS services"
# ---------------------------------------------------------------------------
# This is the step the development environment could not do. Its output is
# what gets pasted back so the parcel, PLSS and forestry providers can be
# wired to the real field names.
docker compose exec -T api python -m scripts.probe_gis --county lassen --search-parcels \
    > /root/gis-probe.txt 2>&1 || warn "the probe reported errors — see /root/gis-probe.txt"

cat <<EOF

============================================================================
Done.

  Admin token   $(grep '^TRACKER_ADMIN_TOKEN=' .env | cut -d= -f2)
  Admin panel   http://127.0.0.1:8000/admin   (not yet public — see below)
  Public site   http://127.0.0.1:3000

Next, in order:

  1. Paste the GIS probe output back into the Claude session:

         cat /root/gis-probe.txt

     That is what lets the parcel maps and radius search be wired up.

  2. Point a DNS A record at this droplet, then follow the reverse-proxy
     section of docs/DEPLOYMENT.md to put Caddy in front with TLS. Until
     then nothing is reachable from the internet, which is intentional.

  3. Set up backups — docs/DEPLOYMENT.md has a nightly pg_dump cron.

Useful commands:

  docker compose logs -f api        follow the API log
  docker compose ps                 service health
  docker compose restart api        restart after an .env change
============================================================================
EOF
