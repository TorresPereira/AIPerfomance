#!/data/data/com.termux/files/usr/bin/bash
# Verifica se o app pediu para rodar o briefing ou exportar um treino
# (botões ▶ e ⌚ no app). Rode isso a cada 1 min via cron.
set -e
cd "$(dirname "$0")"

STATE_FILE="$HOME/.tp_last_trigger_ts"
LAST_TS=0
[ -f "$STATE_FILE" ] && LAST_TS=$(cat "$STATE_FILE")

git fetch origin main --quiet || exit 0
git reset --hard origin/main --quiet

TRIGGER_FILE="pwa/trigger.json"
[ -f "$TRIGGER_FILE" ] || exit 0

TS=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('ts',0))" 2>/dev/null || echo 0)
ACTION=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('action',''))" 2>/dev/null || echo "")
DIA=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('dia','A'))" 2>/dev/null || echo "A")
DATA=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('data',''))" 2>/dev/null || echo "")

if [ "$TS" -gt "$LAST_TS" ] 2>/dev/null; then
  echo "[$(date '+%H:%M:%S')] Novo trigger: $ACTION (ts=$TS)"
  echo "$TS" > "$STATE_FILE"

  case "$ACTION" in
    briefing)
      bash termux_run_briefing.sh
      ;;
    export_gym)
      bash termux_run_export_gym.sh "$DIA"
      ;;
    sync_plano)
      bash termux_run_sync_plano.sh
      ;;
    sync_plano_dia)
      bash termux_run_sync_plano.sh "$DATA"
      ;;
    gerar_plano)
      bash termux_run_gerar_plano.sh
      ;;
    *)
      echo "  Ação desconhecida: $ACTION"
      ;;
  esac
fi
