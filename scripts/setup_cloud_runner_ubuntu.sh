#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${WALMART_PROFILE_DIR:-$HOME/scraper_profiles/walmart_sc_toreo}"

echo "==> Instalando escritorio ligero, RDP y display virtual"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git python3 python3-venv python3-pip \
  xvfb xauth xrdp xfce4 xfce4-goodies dbus-x11

mkdir -p "$PROFILE_DIR"
chmod 700 "$HOME/scraper_profiles" "$PROFILE_DIR" 2>/dev/null || true

# Sesión gráfica para acceso manual por Azure Bastion/RDP.
printf '%s\n' 'startxfce4' > "$HOME/.xsession"
chmod 600 "$HOME/.xsession"
sudo systemctl enable --now xrdp

# Dependencias de Python/Chromium del proyecto.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install --with-deps chromium

echo
echo "Preparación base terminada."
echo "Perfil Walmart: $PROFILE_DIR"
echo
echo "Siguientes pasos:"
echo "1. Registra ESTA VM como GitHub self-hosted runner Linux x64 y agrega la etiqueta: cloud-scraper"
echo "2. Ejecuta el runner con el mismo usuario Linux que creó el perfil."
echo "3. Conéctate a la VM por Azure Bastion usando RDP."
echo "4. En esa sesión gráfica ejecuta:"
echo "   scripts/setup_walmart_session_linux.sh \"$PROFILE_DIR\""
echo "5. En GitHub define WALMART_PROFILE_DIR=$PROFILE_DIR (opcional; esta es la ruta predeterminada)."
