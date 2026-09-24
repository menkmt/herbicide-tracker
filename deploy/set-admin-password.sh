#!/usr/bin/env bash
#
# Set (or change) the browser login for the admin dashboard.
#
#   bash /opt/tracker/deploy/set-admin-password.sh 'the password'
#
# Username is always "admin". The password is stored only as a bcrypt hash
# in .env; nothing on disk can be turned back into it. If Caddy is running it
# is reloaded so the change takes effect at once.

set -euo pipefail

PASSWORD="${1:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tracker}"

log()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mXX\033[0m %s\n' "$*" >&2; exit 1; }

if [ -z "$PASSWORD" ]; then
    # Prompt rather than fail, so the password need not sit in shell history.
    read -r -s -p "New admin password: " PASSWORD; printf '\n'
    read -r -s -p "Again: " AGAIN; printf '\n'
    [ "$PASSWORD" = "$AGAIN" ] || die "the two entries differ"
fi
[ "${#PASSWORD}" -ge 8 ] || die "use at least 8 characters"
cd "$INSTALL_DIR" || die "no checkout at $INSTALL_DIR"
[ -f .env ] || die "no .env — run deploy/bootstrap-droplet.sh first"

log "Hashing"
HASH=$(printf '%s' "$PASSWORD" | docker run --rm -i caddy:2-alpine caddy hash-password 2>/dev/null | tr -d '\r\n')
case "$HASH" in
    '$2'*) ;;
    *) die "hashing failed: $HASH" ;;
esac

# Single-quoted in .env so compose keeps the $ signs literally.
if grep -q "^TRACKER_ADMIN_BASIC_HASH=" .env; then
    sed -i "s|^TRACKER_ADMIN_BASIC_HASH=.*|TRACKER_ADMIN_BASIC_HASH='$HASH'|" .env
else
    printf "TRACKER_ADMIN_BASIC_HASH='%s'\n" "$HASH" >> .env
fi
printf '    stored in .env\n'

# Recreate rather than reload: the hash reaches Caddy as an environment
# variable, and a container that is crash-looping on a bad config would not
# show as "running", so do not condition on that.
if grep -q "^COMPOSE_PROFILES=.*public" .env; then
    log "Restarting the proxy"
    docker compose up -d --force-recreate caddy
    sleep 3
    docker compose ps caddy
fi

cat <<EOT

Done. Sign in at https://api.$(grep '^TRACKER_DOMAIN=' .env | cut -d= -f2)/admin
  username  admin
  password  the one you just set
EOT
