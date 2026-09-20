#!/data/data/com.termux/files/usr/bin/bash
# Roda o briefing diário pelo IP residencial (evita bloqueio 429 do Garmin).
set -e
cd "$(dirname "$0")"

set -a
[ -f .env ] && source .env
set +a

echo "[$(date '+%Y-%m-%d %H:%M')] Iniciando briefing via Termux..."

git pull --quiet || true

python garmin_briefing.py

mkdir -p docs
cp -r pwa/. docs/
touch docs/.nojekyll

git add -f pwa/report.json pwa/plano.json docs/ 2>/dev/null || git add -f pwa/report.json docs/
if git diff --staged --quiet; then
  echo "Sem alterações para commitar."
else
  git commit -m "📊 briefing $(date +'%Y-%m-%d %H:%M') (Termux)"
  git push origin HEAD:main
  echo "✅ report.json publicado."
fi
