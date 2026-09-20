#!/data/data/com.termux/files/usr/bin/bash
# Instala os agendamentos: briefing diário às 08:00 e verificação de
# gatilhos do app a cada 1 minuto. Requer cronie (instalado pelo setup).
set -e
REPO_DIR="$HOME/AIPerfomance"

echo "Configurando crontab..."
( crontab -l 2>/dev/null | grep -v "termux_run_briefing.sh\|termux_poll_trigger.sh" ; \
  echo "0 8 * * * bash $REPO_DIR/termux_run_briefing.sh >> $HOME/.tp_briefing.log 2>&1" ; \
  echo "* * * * * bash $REPO_DIR/termux_poll_trigger.sh >> $HOME/.tp_poll.log 2>&1" \
) | crontab -

echo "Iniciando o daemon crond..."
crond 2>/dev/null || true

echo ""
echo "✅ Agendado:"
echo "   08:00 todo dia         → briefing completo"
echo "   a cada 1 min           → verifica se o app pediu para rodar algo"
echo ""
echo "IMPORTANTE — para o cron sobreviver ao reboot do Android:"
echo "1. Instale o app 'Termux:Boot' (F-Droid ou GitHub oficial do Termux)"
echo "2. Crie ~/.termux/boot/start-crond.sh com o conteúdo:"
echo "   #!/data/data/com.termux/files/usr/bin/bash"
echo "   crond"
echo "3. chmod +x ~/.termux/boot/start-crond.sh"
echo ""
echo "Logs em: ~/.tp_briefing.log e ~/.tp_poll.log"
