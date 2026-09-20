#!/data/data/com.termux/files/usr/bin/bash
# Exporta o treino de academia (A/B/C/F) para o Garmin. Recebe o dia como $1.
set -e
cd "$(dirname "$0")"

set -a
[ -f .env ] && source .env
set +a

DIA="${1:-A}"
echo "[$(date '+%Y-%m-%d %H:%M')] Exportando treino $DIA via Termux..."

git pull --quiet || true

DIA="$DIA" python export_gym.py
