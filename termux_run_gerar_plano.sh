#!/data/data/com.termux/files/usr/bin/bash
# Fluxo completo do "Gerar Plano de 4 Semanas": busca as zonas reais do
# atleta no Garmin, gera o plano com a IA já ciente delas, e envia tudo
# pro relógio — em uma única sessão autenticada no Garmin.
set -e
cd "$(dirname "$0")"

set -a
[ -f .env ] && source .env
set +a

echo "[$(date '+%Y-%m-%d %H:%M')] Gerando plano de 4 semanas via Termux..."

git fetch origin main --quiet
git reset --hard origin/main --quiet

echo "── 1/3: Buscando zonas de treino no Garmin ──"
python buscar_zonas_garmin.py || echo "  ⚠️ Zonas não atualizadas — segue com o que já existe (se houver)."

echo "── 2/3: Gerando plano de 4 semanas (IA) ──"
python gerar_plano.py

echo "── 3/3: Enviando treinos para o Garmin ──"
python sync_plano_garmin.py || echo "  ⚠️ Falha ao sincronizar com o Garmin — plano foi gerado mesmo assim."

mkdir -p docs
cp -r pwa/. docs/
touch docs/.nojekyll

git add -f pwa/zonas_atleta.json pwa/plano.json pwa/plano_garmin_ids.json docs/ 2>/dev/null || \
  git add -f pwa/plano.json docs/
if git diff --staged --quiet; then
  echo "Sem alterações para commitar."
else
  git commit -m "📋 plano de 4 semanas $(date +'%Y-%m-%d %H:%M') (Termux)"
  if ! git push origin HEAD:main; then
    echo "  ⚠️ Push rejeitado (outro processo escreveu ao mesmo tempo) — tentando de novo..."
    git fetch origin main --quiet
    git push --force-with-lease origin HEAD:main
  fi
  echo "✅ Plano publicado."
fi
