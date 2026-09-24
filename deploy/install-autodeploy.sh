#!/usr/bin/env bash
#
# Install the auto-deploy timer. Run once, as root:
#
#   bash /opt/tracker/deploy/install-autodeploy.sh
#
# After this, every push to the tracked branch on GitHub is live on this
# server within about five minutes, with no further typing.

set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/tracker}"
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

cat > /etc/systemd/system/tracker-deploy.service <<UNIT
[Unit]
Description=Herbicide Tracker: pull and deploy if GitHub has new commits
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/bash $INSTALL_DIR/deploy/autodeploy.sh
UNIT

cat > /etc/systemd/system/tracker-deploy.timer <<UNIT
[Unit]
Description=Check GitHub for new commits every 3 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=3min
RandomizedDelaySec=30s

[Install]
WantedBy=timers.target
UNIT

touch /var/log/tracker-deploy.log
systemctl daemon-reload
systemctl enable --now tracker-deploy.timer
systemctl start tracker-deploy.service

echo
echo "Installed. Pushes to GitHub now deploy themselves within ~3 minutes."
echo "  progress:  tail -f /var/log/tracker-deploy.log"
echo "  status:    systemctl status tracker-deploy.timer"
