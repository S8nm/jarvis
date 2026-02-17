#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# JARVIS Pi Worker — One-command setup script
# Run on the Raspberry Pi: sudo bash setup.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

JARVIS_USER="jarvis"
JARVIS_HOME="/home/${JARVIS_USER}"
INSTALL_DIR="${JARVIS_HOME}/jarvis-pi"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[JARVIS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ──────────────────────────── Checks ────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run as root (sudo bash setup.sh)"
fi

if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null && ! grep -q "BCM" /proc/cpuinfo 2>/dev/null; then
    warn "This doesn't look like a Raspberry Pi. Continuing anyway..."
fi

log "Starting JARVIS Pi Worker setup..."

# ──────────────────────────── 1. Create User ────────────────────────────
if id "${JARVIS_USER}" &>/dev/null; then
    log "User '${JARVIS_USER}' already exists"
else
    log "Creating user '${JARVIS_USER}'..."
    useradd -m -s /bin/bash "${JARVIS_USER}"
fi

# Add to hardware groups
for group in gpio i2c spi dialout; do
    if getent group "${group}" &>/dev/null; then
        usermod -aG "${group}" "${JARVIS_USER}" 2>/dev/null || true
        log "Added ${JARVIS_USER} to ${group} group"
    fi
done

# ──────────────────────────── 2. System Dependencies ────────────────────────────
log "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    python3-gpiod python3-smbus2 \
    i2c-tools \
    ufw

# Enable I2C and SPI if not already
if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_i2c 0 2>/dev/null || true
    raspi-config nonint do_spi 0 2>/dev/null || true
    log "I2C and SPI interfaces enabled"
fi

# ──────────────────────────── 3. Install JARVIS Pi Worker ────────────────────────────
log "Installing JARVIS Pi Worker to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp -r "${SCRIPT_DIR}/dispatcher.py" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/config.json" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/tools" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"
mkdir -p "${INSTALL_DIR}/scripts"

# Install Python dependencies (system-wide for the service)
pip3 install --break-system-packages -q -r "${INSTALL_DIR}/requirements.txt" 2>/dev/null || \
    pip3 install -q -r "${INSTALL_DIR}/requirements.txt"

chown -R "${JARVIS_USER}:${JARVIS_USER}" "${INSTALL_DIR}"
log "Worker files installed"

# ──────────────────────────── 4. Install PicoClaw (optional) ────────────────────────────
if command -v picoclaw &>/dev/null; then
    log "PicoClaw already installed: $(picoclaw --version 2>/dev/null || echo 'unknown')"
else
    log "PicoClaw not found. Install manually if needed:"
    warn "  curl -fsSL https://picoclaw.dev/install.sh | bash"
    warn "  Or: go install github.com/picoclaw/picoclaw@latest"
fi

# ──────────────────────────── 5. Systemd Services ────────────────────────────
log "Installing systemd services..."
cp "${SCRIPT_DIR}/systemd/jarvis-dispatcher.service" /etc/systemd/system/
chmod 644 /etc/systemd/system/jarvis-dispatcher.service

if command -v picoclaw &>/dev/null; then
    cp "${SCRIPT_DIR}/systemd/picoclaw-gateway.service" /etc/systemd/system/
    chmod 644 /etc/systemd/system/picoclaw-gateway.service
fi

systemctl daemon-reload

# Enable and start dispatcher
systemctl enable jarvis-dispatcher.service
systemctl start jarvis-dispatcher.service
log "jarvis-dispatcher service started"

# Enable PicoClaw gateway if available
if command -v picoclaw &>/dev/null; then
    systemctl enable picoclaw-gateway.service
    systemctl start picoclaw-gateway.service
    log "picoclaw-gateway service started"
fi

# ──────────────────────────── 6. Firewall ────────────────────────────
log "Configuring firewall..."
ufw --force reset >/dev/null 2>&1
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
# Dispatcher binds to 127.0.0.1 only — no external firewall rule needed
ufw --force enable
log "Firewall configured (SSH only, dispatcher is localhost-bound)"

# ──────────────────────────── 7. SSH Key Setup ────────────────────────────
SSH_DIR="${JARVIS_HOME}/.ssh"
if [ ! -d "${SSH_DIR}" ]; then
    mkdir -p "${SSH_DIR}"
    chmod 700 "${SSH_DIR}"
    touch "${SSH_DIR}/authorized_keys"
    chmod 600 "${SSH_DIR}/authorized_keys"
    chown -R "${JARVIS_USER}:${JARVIS_USER}" "${SSH_DIR}"
    log "SSH directory created. Add your PC's public key to ${SSH_DIR}/authorized_keys"
fi

# ──────────────────────────── Done ────────────────────────────
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "════════════════════════════════════════════════════════════"
echo " JARVIS Pi Worker Setup Complete!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo " Pi IP Address:  ${PI_IP}"
echo " Dispatcher:     http://127.0.0.1:18790 (localhost only)"
echo " SSH User:       ${JARVIS_USER}"
echo ""
echo " PC-side config.json — add this section:"
echo ""
echo "   \"pi\": {"
echo "       \"host\": \"${PI_IP}\","
echo "       \"user\": \"${JARVIS_USER}\","
echo "       \"ssh_key\": \"~/.ssh/jarvis_pi\","
echo "       \"transport\": \"ssh\","
echo "       \"gateway_port\": 18790,"
echo "       \"tunnel_local_port\": 18790"
echo "   }"
echo ""
echo " Next steps:"
echo "   1. On PC: ssh-keygen -t ed25519 -f ~/.ssh/jarvis_pi -N ''"
echo "   2. On PC: ssh-copy-id -i ~/.ssh/jarvis_pi.pub ${JARVIS_USER}@${PI_IP}"
echo "   3. Test:  ssh -i ~/.ssh/jarvis_pi ${JARVIS_USER}@${PI_IP} 'python3 ~/jarvis-pi/dispatcher.py --task \"{\\\"task_name\\\":\\\"system_info\\\",\\\"args\\\":{\\\"check\\\":\\\"all\\\"}}\"'"
echo ""
echo "════════════════════════════════════════════════════════════"
