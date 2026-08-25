#!/usr/bin/env bash
# Provision a fresh Ubuntu box to run the Kalshi collector as a service.
#
# Idempotent: safe to re-run after editing a unit file or updating the repo.
# Run as a sudo-capable user, NOT as root.
#
#   sudo ./deploy/install.sh
#
# It deliberately does NOT touch .env or certs/. Credentials are moved by hand,
# by you, in one scp - see deploy/README.md.

set -euo pipefail

APP_DIR=/opt/quant-research
SERVICE_USER=collector

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential \
                       systemd-timesyncd unattended-upgrades rsync

echo "==> clock"
# Kalshi signs a millisecond timestamp. A drifted clock returns 401 on every
# request, which is indistinguishable from a bad key until you think to check.
timedatectl set-ntp true
timedatectl status | sed -n '1,8p'

echo "==> swap"
# The Always Free AMD micro shape has 1 GB of RAM and Oracle's Ubuntu images
# ship with no swap at all. That is enough to RUN the collector, which streams
# to disk and holds almost nothing, but not always enough to pip-install into
# on a bad day. A 2 GB swapfile costs nothing on a 47 GB volume and turns a
# hard OOM kill into a slow minute. Skipped on anything with real memory.
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if (( mem_mb < 2048 )) && [[ ! -f /swapfile ]]; then
    echo "    ${mem_mb}MB RAM detected, creating a 2G swapfile"
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap -q /swapfile
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
    echo "    ${mem_mb}MB RAM, no swapfile needed"
fi

echo "==> service account"
# A dedicated unprivileged user with no login shell. The key on this box can
# place trades; it should not also be the account you SSH in as.
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> application directory"
mkdir -p "$APP_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

if [[ ! -d "$APP_DIR/.venv" ]]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -q \
    -r "$APP_DIR/deploy/requirements-collector.txt"

echo "==> permissions on secrets"
# Readable by the service account and nobody else. This is the whole reason the
# service does not run as your login user.
for secret in "$APP_DIR/.env" "$APP_DIR/certs"; do
    if [[ -e "$secret" ]]; then
        chown -R "$SERVICE_USER:$SERVICE_USER" "$secret"
        chmod -R go-rwx "$secret"
        echo "    secured $secret"
    else
        echo "    WARNING: $secret not found - copy it before starting the service"
    fi
done
chmod +x "$APP_DIR/deploy/healthcheck.sh"

echo "==> systemd units"
install -m 644 "$APP_DIR/deploy/kalshi-collector.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/kalshi-watchdog.service"  /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/kalshi-watchdog.timer"    /etc/systemd/system/
systemctl daemon-reload

echo "==> log rotation"
# journald default is 4GB or 10% of the volume, whichever is smaller. On a 25GB
# free-tier disk that competes with the data for space, and the data is the
# thing that cannot be regenerated.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/kalshi.conf <<'EOF'
[Journal]
SystemMaxUse=500M
MaxRetentionSec=2week
EOF
systemctl restart systemd-journald

echo "==> unattended security updates"
# Security patches only, and no automatic reboots: a surprise reboot mid-run is
# exactly the kind of silent gap this whole exercise is trying to eliminate.
cat > /etc/apt/apt.conf.d/51kalshi-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF

echo
echo "=========================================================="
echo " Installed. Nothing is running yet."
echo
echo " Confirm .env and certs/ are present, then:"
echo "   sudo systemctl enable --now kalshi-collector"
echo "   sudo systemctl enable --now kalshi-watchdog.timer"
echo
echo " Watch it:"
echo "   journalctl -u kalshi-collector -f"
echo "=========================================================="
