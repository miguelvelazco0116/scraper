#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${1:-${WALMART_PROFILE_DIR:-$HOME/scraper_profiles/walmart_sc_toreo}}"

if [[ -z "${DISPLAY:-}" ]]; then
  echo "No hay una sesión gráfica activa."
  echo "Conéctate a la VM por Azure Bastion usando RDP y vuelve a ejecutar este script desde una terminal del escritorio."
  exit 1
fi

mkdir -p "$PROFILE_DIR"
chmod 700 "$PROFILE_DIR"

if [[ -d .venv ]]; then
  source .venv/bin/activate
fi

python scripts/walmart_prepare_session.py \
  --profile-dir "$PROFILE_DIR" \
  --store-id 2344

echo
echo "Sesión persistente preparada en: $PROFILE_DIR"
echo "La próxima ejecución de Full Scraper - Cloud VM reutilizará este perfil."
