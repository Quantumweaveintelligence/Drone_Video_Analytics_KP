#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# setup_ubuntu.sh — DVAS Ubuntu Ingest Server Bootstrap
# File: ingest/scripts/setup_ubuntu.sh
#
# Run once on a fresh Ubuntu 22.04 LTS machine to install all prerequisites:
#   - System packages + WireGuard
#   - NVIDIA drivers + CUDA toolkit
#   - Docker (server edition, not Docker Desktop)
#   - NVIDIA Container Toolkit
#   - WireGuard server configuration
#
# Usage:
#   chmod +x ingest/scripts/setup_ubuntu.sh
#   sudo ./ingest/scripts/setup_ubuntu.sh
#
# After this script completes:
#   1. Copy wg0-server.conf: sudo cp config/wireguard/wg0-server.conf /etc/wireguard/wg0.conf
#   2. Edit /etc/wireguard/wg0.conf — fill in the private key and ethernet interface
#   3. Copy .env: cp ingest/.env.example .env && nano .env
#   4. Start services: docker compose up -d
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warning() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "This script must be run as root (sudo ./setup_ubuntu.sh)"

REAL_USER="${SUDO_USER:-$USER}"

# ── Phase 1: System packages ──────────────────────────────────────────────────
info "Phase 1: Updating system packages..."
apt update && apt upgrade -y
apt install -y build-essential git curl wget wireguard ufw net-tools

# ── Phase 2: NVIDIA drivers ───────────────────────────────────────────────────
info "Phase 2: Installing NVIDIA drivers..."
if command -v nvidia-smi &>/dev/null; then
    info "NVIDIA driver already installed — skipping."
else
    apt install -y ubuntu-drivers-common
    ubuntu-drivers autoinstall
    warning "NVIDIA driver installed. A reboot may be required before continuing."
    warning "After reboot, re-run this script to continue from Phase 3."
fi

# ── Phase 3: Docker (server edition) ─────────────────────────────────────────
info "Phase 3: Installing Docker..."
if command -v docker &>/dev/null; then
    info "Docker already installed — skipping."
else
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "$REAL_USER"
    apt install -y docker-compose-plugin
    info "Docker installed. Note: log out and back in for group membership to take effect."
fi

# ── Phase 4: NVIDIA Container Toolkit ────────────────────────────────────────
info "Phase 4: Installing NVIDIA Container Toolkit..."
if dpkg -l | grep -q nvidia-container-toolkit; then
    info "NVIDIA Container Toolkit already installed — skipping."
else
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt update
    apt install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
    info "NVIDIA Container Toolkit installed."
fi

# ── Phase 5: Verify GPU in Docker ────────────────────────────────────────────
info "Phase 5: Verifying GPU access from Docker..."
if docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi 2>/dev/null; then
    info "GPU is accessible from Docker containers."
else
    warning "GPU not accessible from Docker yet. This may require a reboot after NVIDIA driver install."
fi

# ── Phase 6: WireGuard server setup ──────────────────────────────────────────
info "Phase 6: Setting up WireGuard gateway..."

if [[ -f /etc/wireguard/server-private.key ]]; then
    info "WireGuard keypair already exists — skipping key generation."
else
    wg genkey | tee /etc/wireguard/server-private.key | wg pubkey > /etc/wireguard/server-public.key
    chmod 600 /etc/wireguard/server-private.key
    info "WireGuard keypair generated."
    info "Server PUBLIC key (share with laptops and Headscale):"
    cat /etc/wireguard/server-public.key
fi

# Enable IP forwarding
if ! grep -q "net.ipv4.ip_forward = 1" /etc/sysctl.conf; then
    echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf
    sysctl -p
    info "IP forwarding enabled."
fi

# ── Phase 7: Firewall ─────────────────────────────────────────────────────────
info "Phase 7: Configuring UFW firewall..."
ufw --force enable
ufw allow 22/tcp              # SSH — keep this open!
ufw allow 51820/udp           # WireGuard VPN
ufw allow from 10.10.20.0/24 to any port 443   # NGINX (VPN clients only)
ufw allow from 10.10.20.0/24 to any port 8080  # Keycloak (VPN clients only)
ufw allow from 10.10.20.0/24 to any port 8081  # Headscale (VPN clients only)
ufw allow from 127.0.0.1 to any port 9090      # Prometheus (localhost only)
ufw allow from 127.0.0.1 to any port 9093      # Alertmanager (localhost only)
ufw allow from 127.0.0.1 to any port 9000      # step-ca (localhost only)
ufw status verbose
info "Firewall configured."

# ── Phase 8: /etc/hosts entry ─────────────────────────────────────────────────
info "Phase 8: Adding ingest.dvas.internal to /etc/hosts..."
if ! grep -q "ingest.dvas.internal" /etc/hosts; then
    echo "10.10.20.1  ingest.dvas.internal" >> /etc/hosts
    info "Added ingest.dvas.internal → 10.10.20.1"
else
    info "/etc/hosts entry already exists."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "  DVAS Ubuntu Bootstrap Complete"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo "  1. Fill in /etc/wireguard/wg0.conf with your private key:"
echo "       sudo cp config/wireguard/wg0-server.conf /etc/wireguard/wg0.conf"
echo "       sudo nano /etc/wireguard/wg0.conf"
echo "       # Paste contents of /etc/wireguard/server-private.key into PrivateKey"
echo "       # Set <ETH_INTERFACE> to your interface (check: ip route | grep default)"
echo ""
echo "  2. Start the WireGuard gateway:"
echo "       sudo systemctl enable --now wg-quick@wg0"
echo "       sudo wg show"
echo ""
echo "  3. Set up Docker environment:"
echo "       cp ingest/.env.example .env"
echo "       nano .env   # Fill in real passwords"
echo ""
echo "  4. Start all services:"
echo "       docker compose up -d"
echo "       docker compose ps"
echo ""
echo "  5. Bootstrap Keycloak (run the setup service):"
echo "       docker compose logs setup"
echo ""
echo "  6. Get the step-ca root CA certificate (needed on every Windows laptop):"
echo "       docker compose exec step-ca cat /home/step/certs/root_ca.crt"
echo ""
echo "  Server public WireGuard key:"
cat /etc/wireguard/server-public.key 2>/dev/null || echo "  (not generated yet — run after Phase 6)"
echo "════════════════════════════════════════════════════════════════════════"
