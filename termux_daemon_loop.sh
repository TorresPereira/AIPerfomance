#!/data/data/com.termux/files/usr/bin/bash
# Substitui o cronie/crond, que não funciona em alguns Androids.
# Roda para sempre em segundo plano: verifica gatilhos do app a cada
# 1 min, e roda o briefing completo uma vez por dia às 08:00.
#
# Iniciar:  nohup bash termux_daemon_loop.sh > ~/.tp_daemon.log 2>&1 &
#           disown
# Verificar se está rodando:  pgrep -f termux_daemon_loop.sh
# Parar:                      pkill -f termux_daemon_loop.sh

cd "$(dirname "$0")"
LAST_BRIEFING_FILE="$HOME/.tp_last_briefing_date"

echo "[$(date '+%Y-%m-%d %H:%M')] Daemon iniciado (PID $$)."

while true; do
  # ── 1. Verifica gatilhos do app (▶, ⌚, plano) ──────────────────────────────
  bash termux_poll_trigger.sh

  # ── 2. Briefing diário — roda uma vez, na primeira checagem após as 08:00 ──
  HOJE=$(date +%Y-%m-%d)
  HORA=$(date +%H)
  ULTIMO=""
  [ -f "$LAST_BRIEFING_FILE" ] && ULTIMO=$(cat "$LAST_BRIEFING_FILE")

  if [ "$HORA" -ge 8 ] && [ "$ULTIMO" != "$HOJE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M')] Rodando briefing diário..."
    bash termux_run_briefing.sh
    echo "$HOJE" > "$LAST_BRIEFING_FILE"
  fi

  sleep 60
done
