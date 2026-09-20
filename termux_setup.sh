#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Setup único do TP Performance Coach no Termux (Android).
# Roda o briefing e o export de treino pelo SEU IP residencial, evitando o
# bloqueio 429 que o Garmin aplica aos IPs compartilhados do GitHub Actions.
#
# Uso:
#   pkg install git python -y   (se ainda não tiver)
#   bash termux_setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

REPO_DIR="$HOME/AIPerfomance"
REPO_URL="https://github.com/TorresPereira/AIPerfomance.git"

echo "=== TP Performance Coach — Setup Termux ==="

echo "[1/5] Instalando pacotes do sistema..."
pkg update -y >/dev/null 2>&1 || true
pkg install -y python git cronie >/dev/null

echo "[2/5] Clonando/atualizando repositório..."
if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && git pull
else
  git clone "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

echo "[3/5] Instalando dependências Python..."
pip install --upgrade pip >/dev/null
pip install "garminconnect>=0.2.22,<0.3" >/dev/null

echo "[4/5] Criando arquivo de configuração (.env)..."
if [ ! -f "$REPO_DIR/.env" ]; then
  cat > "$REPO_DIR/.env" << 'EOF'
GARMIN_EMAIL=seu_email@exemplo.com
GARMIN_PASSWORD=sua_senha
ANTHROPIC_API_KEY=sk-ant-...
RACE_DATE=
NTFY_TOPIC=
GARMIN_CACHE_DIR=/data/data/com.termux/files/home/.garmin_cache
EOF
  echo "  ⚠️  Edite $REPO_DIR/.env com suas credenciais antes de continuar!"
else
  echo "  .env já existe, mantido como está."
fi

echo "[5/5] Configurando push para o GitHub..."
echo "  Cole seu Personal Access Token do GitHub (fine-grained, Contents: Read & write):"
read -r -s GH_TOKEN
git remote set-url origin "https://${GH_TOKEN}@github.com/TorresPereira/AIPerfomance.git"
git config user.name  "termux-bot"
git config user.email "termux-bot@local"

echo ""
echo "✅ Setup concluído em $REPO_DIR"
echo ""
echo "PRÓXIMOS PASSOS:"
echo "1. Edite $REPO_DIR/.env com email/senha do Garmin e sua ANTHROPIC_API_KEY"
echo "2. Teste manualmente:  bash $REPO_DIR/termux_run_briefing.sh"
echo "3. Agende os cron jobs:  bash $REPO_DIR/termux_cron_install.sh"
