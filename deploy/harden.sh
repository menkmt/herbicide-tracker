#!/usr/bin/env bash
#
# Host hardening for the droplet. Run once as root after bootstrap; safe to
# re-run.
#
#   bash /opt/tracker/deploy/harden.sh
#
# What it does:
#   - SSH: keys only, no passwords, no root password login. Refuses to do
#     this unless a key is already authorised, so you cannot lock yourself out.
#   - fail2ban: bans addresses that hammer SSH.
#   - unattended-upgrades: security patches install themselves.
#   - Docker: confirms every published port is loopback-only except Caddy's.
#
# What it does not do: anything to the application. The app's own controls
# (admin token, rate limits, page-size caps, non-root containers) live in the
# code and the compose file.

set -euo pipefail

log()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mXX\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root"
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
log "SSH: keys only"
# ---------------------------------------------------------------------------
if [ -s /root/.ssh/authorized_keys ] && grep -qE '^(ssh|ecdsa)-' /root/.ssh/authorized_keys; then
    cat > /etc/ssh/sshd_config.d/90-tracker-hardening.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
MaxAuthTries 4
X11Forwarding no
CONF
    sshd -t && systemctl reload ssh 2>/dev/null || systemctl reload sshd
    printf '    password login disabled; %s authorised key(s)\n' "$(grep -cE '^(ssh|ecdsa)-' /root/.ssh/authorized_keys)"
else
    warn "no SSH key in /root/.ssh/authorized_keys — leaving password login ON"
    warn "add your public key there first, then re-run this script"
fi

# ---------------------------------------------------------------------------
log "fail2ban"
# ---------------------------------------------------------------------------
apt-get install -y -qq fail2ban >/dev/null
cat > /etc/fail2ban/jail.d/tracker.conf <<'CONF'
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
CONF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban
printf '    sshd jail active\n'

# ---------------------------------------------------------------------------
log "Automatic security updates"
# ---------------------------------------------------------------------------
apt-get install -y -qq unattended-upgrades >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
systemctl enable --now unattended-upgrades >/dev/null
printf '    enabled\n'

# ---------------------------------------------------------------------------
log "Checking what is listening publicly"
# ---------------------------------------------------------------------------
# Docker's iptables rules sit ahead of ufw, so this — not ufw — is the check
# that matters. Anything published on 0.0.0.0 other than 80/443 is a problem.
BAD=0
while read -r line; do
    port=$(printf '%s' "$line" | sed -E 's/.*0\.0\.0\.0:([0-9]+).*/\1/')
    case "$port" in
        80|443) ;;
        *) printf '    PUBLIC: %s\n' "$line"; BAD=1 ;;
    esac
done < <(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | tr ',' '\n' | grep '0\.0\.0\.0:' || true)
if [ "$BAD" = "0" ]; then
    printf '    only 80/443 are public; everything else is loopback\n'
else
    warn "a container publishes a port on all interfaces. cd /opt/tracker && git pull && docker compose up -d"
fi

log "Done"
