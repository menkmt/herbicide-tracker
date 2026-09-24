#!/usr/bin/env bash
#
# Put the tracker on the internet at a domain, with HTTPS.
#
#   bash /opt/tracker/deploy/enable-domain.sh herbicidetracker.com you@example.com
#
# Before running it, create three DNS A records pointing at this server:
#
#   @      -> this server's IP      (the public site)
#   www    -> this server's IP      (redirects to the site)
#   api    -> this server's IP      (the API and the admin dashboard)
#
# The script checks the records resolve here first, because Caddy cannot get
# a certificate for a name that points elsewhere. Safe to re-run.

set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tracker}"

log()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mXX\033[0m %s\n' "$*" >&2; exit 1; }

[ -n "$DOMAIN" ] || die "usage: enable-domain.sh DOMAIN [EMAIL]"
case "$DOMAIN" in
    http*|*/*|www.*) die "give the bare domain, e.g. herbicidetracker.com" ;;
esac
# Caddy's ACME account needs an address; Let's Encrypt uses it only for
# expiry warnings, so a role address at the domain is a sensible default.
EMAIL="${EMAIL:-admin@$DOMAIN}"
cd "$INSTALL_DIR" || die "no checkout at $INSTALL_DIR"
[ -f .env ] || die "no .env — run deploy/bootstrap-droplet.sh first"

# ---------------------------------------------------------------------------
log "Checking DNS"
# ---------------------------------------------------------------------------
MY_IP=$(curl -fsS -4 https://api.ipify.org 2>/dev/null || curl -fsS -4 https://ifconfig.me 2>/dev/null || true)
[ -n "$MY_IP" ] || die "could not determine this server's public IP"
printf '    this server is %s\n' "$MY_IP"

DNS_OK=1
for name in "$DOMAIN" "www.$DOMAIN" "api.$DOMAIN"; do
    # Authoritative-ish lookup via a public resolver so a stale local cache
    # cannot make a freshly created record look missing.
    if command -v dig >/dev/null 2>&1; then
        got=$(dig +short A "$name" @1.1.1.1 2>/dev/null | tr '\n' ' ' || true)
    else
        got=$(getent ahostsv4 "$name" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
    fi
    if printf '%s' "$got" | grep -qw "$MY_IP"; then
        printf '    %-30s ok\n' "$name"
    else
        printf '    %-30s -> %s (expected %s)\n' "$name" "${got:-nothing}" "$MY_IP"
        DNS_OK=0
    fi
done
if [ "$DNS_OK" != "1" ]; then
    warn "not every record points here yet. DNS changes can take a few minutes"
    warn "to propagate; re-run this once they do. Nothing has been changed."
    exit 2
fi

# ---------------------------------------------------------------------------
log "Updating .env"
# ---------------------------------------------------------------------------
set_env() {
    # Replace the line if present, append if not.
    if grep -q "^$1=" .env; then
        sed -i "s|^$1=.*|$1=$2|" .env
    else
        printf '%s=%s\n' "$1" "$2" >> .env
    fi
}
set_env TRACKER_DOMAIN "$DOMAIN"
set_env TRACKER_PUBLIC_BASE_URL "https://$DOMAIN"
set_env COMPOSE_PROFILES public
set_env CADDY_EMAIL "$EMAIL"
printf '    TRACKER_DOMAIN=%s\n    TRACKER_PUBLIC_BASE_URL=https://%s\n    COMPOSE_PROFILES=public\n' "$DOMAIN" "$DOMAIN"

# ---------------------------------------------------------------------------
log "Starting the reverse proxy"
# ---------------------------------------------------------------------------
# api and web read the new public URL from the environment; recreate them so
# CORS and the sitemap pick it up. caddy starts because the profile is now on.
docker compose up -d --force-recreate api web caddy

printf '    waiting for a certificate'
CERT_OK=0
for _ in $(seq 1 30); do
    if curl -fsSo /dev/null --max-time 5 "https://api.$DOMAIN/healthz"; then
        CERT_OK=1
        break
    fi
    printf '.'; sleep 4
done
printf '\n'
[ "$CERT_OK" = "1" ] || die "HTTPS did not come up within two minutes — check: docker compose logs caddy"

cat <<EOT

============================================================================
Live.

  Public site   https://$DOMAIN
  Admin panel   https://api.$DOMAIN/admin
  API docs      https://api.$DOMAIN/api/docs
  Admin token   $(grep '^TRACKER_ADMIN_TOKEN=' .env | cut -d= -f2)

Ports 8000 and 3000 remain loopback-only; everything public goes through
Caddy on 443. The SSH tunnel still works if you prefer it for admin.
============================================================================
EOT
