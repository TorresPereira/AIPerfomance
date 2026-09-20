#!/data/data/com.termux/files/usr/bin/bash
# Inicia o daemon em segundo plano (substitui o cron, que falhou neste
# aparelho). Também configura o Termux:Boot para reiniciar sozinho.
set -e
REPO_DIR="$HOME/AIPerfomance"
cd "$REPO_DIR"

echo "Parando qualquer daemon antigo (se houver)..."
pkill -f termux_daemon_loop.sh 2>/dev/null || true
sleep 1

echo "Tentando travar o processador ligado (evita o Android matar o processo)..."
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || \
  echo "  ⚠️ termux-wake-lock não encontrado — instale o app Termux:API para mais estabilidade (opcional)."

echo "Iniciando o daemon..."
nohup bash "$REPO_DIR/termux_daemon_loop.sh" > "$HOME/.tp_daemon.log" 2>&1 &
disown

sleep 2
if pgrep -f termux_daemon_loop.sh >/dev/null; then
  echo "✅ Daemon rodando (PID $(pgrep -f termux_daemon_loop.sh))."
else
  echo "❌ Daemon não iniciou — veja ~/.tp_daemon.log"
  exit 1
fi

echo "Configurando reinício automático (Termux:Boot)..."
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/start-daemon.sh" << EOF
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock 2>/dev/null || true
cd "$REPO_DIR"
nohup bash termux_daemon_loop.sh > "$HOME/.tp_daemon.log" 2>&1 &
EOF
chmod +x "$HOME/.termux/boot/start-daemon.sh"

echo ""
echo "✅ Tudo pronto."
echo "   Verificar se está rodando:  pgrep -f termux_daemon_loop.sh"
echo "   Ver o log:                  tail -f ~/.tp_daemon.log"
echo "   Parar:                      pkill -f termux_daemon_loop.sh"
echo ""
echo "⚠️ Para sobreviver a reinícios do celular, instale o app"
echo "   'Termux:Boot' (mesma loja/fonte que o Termux) — sem ele,"
echo "   se o Android reiniciar o celular, é preciso rodar este"
echo "   script de novo manualmente."
