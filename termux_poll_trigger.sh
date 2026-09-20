#!/data/data/com.termux/files/usr/bin/bash
# Verifica se o app pediu para rodar o briefing ou exportar um treino
# (botões ▶ e ⌚ no app). Rode isso a cada 1 min via cron.
set -e
cd "$(dirname "$0")"

STATE_FILE="$HOME/.tp_last_trigger_ts"
LAST_TS=0
[ -f "$STATE_FILE" ] && LAST_TS=$(cat "$STATE_FILE")

git pull --quiet || exit 0

TRIGGER_FILE="pwa/trigger.json"
[ -f "$TRIGGER_FILE" ] || exit 0

TS=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('ts',0))" 2>/dev/null || echo 0)
ACTION=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('action',''))" 2>/dev/null || echo "")
DIA=$(python3 -c "import json;print(json.load(open('$TRIGGER_FILE')).get('dia','A'))" 2>/dev/null || echo "A")

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
    *)
      echo "  Ação desconhecida: $ACTION"
      ;;
  esac
fi
