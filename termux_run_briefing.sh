#!/data/data/com.termux/files/usr/bin/bash
# Roda o briefing diário pelo IP residencial (evita bloqueio 429 do Garmin).
set -e
cd "$(dirname "$0")"

set -a
[ -f .env ] && source .env
set +a

echo "[$(date '+%Y-%m-%d %H:%M')] Iniciando briefing via Termux..."

git fetch origin main --quiet
git reset --hard origin/main --quiet

python garmin_briefing.py

mkdir -p docs
cp -r pwa/. docs/
touch docs/.nojekyll

git add -f pwa/report.json pwa/plano.json docs/ 2>/dev/null || git add -f pwa/report.json docs/
if git diff --staged --quiet; then
  echo "Sem alterações para commitar."
else
  git commit -m "📊 briefing $(date +'%Y-%m-%d %H:%M') (Termux)"
  if ! git push origin HEAD:main; then
    echo "  ⚠️ Push rejeitado (outro processo escreveu ao mesmo tempo) — tentando de novo..."
    git fetch origin main --quiet
    git push --force-with-lease origin HEAD:main
  fi
  echo "✅ report.json publicado."
fi
