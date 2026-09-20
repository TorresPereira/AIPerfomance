#!/data/data/com.termux/files/usr/bin/bash
# Envia as sessões do plano de 4 semanas para o Garmin Connect.
# Sem argumento: sincroniza tudo a partir de hoje. Com data (YYYY-MM-DD): só aquele dia.
set -e
cd "$(dirname "$0")"

set -a
[ -f .env ] && source .env
set +a

SYNC_DATE="${1:-}"
echo "[$(date '+%Y-%m-%d %H:%M')] Sincronizando plano com o Garmin via Termux... (${SYNC_DATE:-todos os dias futuros})"

git fetch origin main --quiet
git reset --hard origin/main --quiet

SYNC_DATE="$SYNC_DATE" python sync_plano_garmin.py

git add -f pwa/plano_garmin_ids.json 2>/dev/null || true
if ! git diff --staged --quiet; then
  git commit -m "🔗 ids garmin do plano $(date +'%Y-%m-%d %H:%M') (Termux)"
  if ! git push origin HEAD:main; then
    echo "  ⚠️ Push rejeitado (outro processo escreveu ao mesmo tempo) — tentando de novo..."
    git fetch origin main --quiet
    git push --force-with-lease origin HEAD:main
  fi
fi
