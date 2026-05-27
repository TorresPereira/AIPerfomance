#!/usr/bin/env python3
"""
Garmin Daily Briefing
Busca dados do dia via Garmin Connect e envia resumo por e-mail via SendGrid.
"""

import os
import json
import datetime
import traceback
from garminconnect import Garmin

# ─── Configuração via variáveis de ambiente ───────────────────────────────────
GARMIN_EMAIL    = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
SENDGRID_API_KEY= os.environ["SENDGRID_API_KEY"]
EMAIL_FROM      = os.environ["EMAIL_FROM"]      # ex: briefing@seudominio.com
EMAIL_TO        = os.environ["EMAIL_TO"]        # seu e-mail de destino

TODAY = datetime.date.today()
TODAY_STR = TODAY.isoformat()

# ─── Helpers ─────────────────────────────────────────────────────────────────

def semaforo(valor, limites, labels=("🔴", "🟡", "🟢"), inverso=False):
    """
    Retorna emoji de semáforo baseado em limites [baixo, alto].
    inverso=True inverte a lógica (menor = melhor, ex: carga).
    """
    baixo, alto = limites
    if inverso:
        if valor >= alto:   return labels[0]
        if valor >= baixo:  return labels[1]
        return labels[2]
    else:
        if valor >= alto:   return labels[2]
        if valor >= baixo:  return labels[1]
        return labels[0]

def fmt(valor, casas=1, sufixo=""):
    if valor is None:
        return "—"
    return f"{round(valor, casas)}{sufixo}"

# ─── Coleta de dados ──────────────────────────────────────────────────────────

def coletar_dados():
    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()

    dados = {}

    # --- HRV (Heart Rate Variability) ---
    try:
        hrv = api.get_hrv_data(TODAY_STR)
        summary = hrv.get("hrvSummary", {})
        dados["hrv"]        = summary.get("lastNight")
        dados["hrv_status"] = summary.get("hrvStatus", "").lower()  # balanced / unbalanced / poor
    except Exception:
        dados["hrv"] = None
        dados["hrv_status"] = None

    # --- Sono ---
    try:
        sono = api.get_sleep_data(TODAY_STR)
        daily = sono.get("dailySleepDTO", {})
        dados["sono_horas"]   = round((daily.get("sleepTimeSeconds") or 0) / 3600, 1)
        dados["sono_score"]   = daily.get("sleepScores", {}).get("overall", {}).get("value")
        dados["sono_deep_h"]  = round((daily.get("deepSleepSeconds") or 0) / 3600, 1)
        dados["sono_rem_h"]   = round((daily.get("remSleepSeconds") or 0) / 3600, 1)
    except Exception:
        dados["sono_horas"] = dados["sono_score"] = None
        dados["sono_deep_h"] = dados["sono_rem_h"] = None

    # --- Body Battery / Readiness (Garmin usa Body Battery como proxy) ---
    try:
        bb = api.get_body_battery(TODAY_STR, TODAY_STR)
        if bb and isinstance(bb, list):
            # Pega o valor máximo do dia (= início do dia após dormir)
            vals = [x.get("charged") for x in bb if x.get("charged") is not None]
            dados["readiness"] = max(vals) if vals else None
        else:
            dados["readiness"] = None
    except Exception:
        dados["readiness"] = None

    # --- Training Load / ACWR ---
    try:
        stats = api.get_training_status(TODAY_STR)
        load  = stats.get("trainingLoadBalance", {})
        dados["carga_atual"]  = load.get("recentTrainingLoad")
        dados["carga_otima"]  = load.get("optimalTrainingLoad")
        acwr_raw              = load.get("acuteChronicWorkloadRatio")
        dados["acwr"]         = round(acwr_raw, 2) if acwr_raw else None
        dados["training_status"] = stats.get("trainingStatus", {}).get("trainingStatus", "")
    except Exception:
        dados["carga_atual"] = dados["carga_otima"] = dados["acwr"] = None
        dados["training_status"] = None

    # --- VO2max ---
    try:
        perf = api.get_max_metrics(TODAY_STR)
        # Tenta VO2max de corrida primeiro, depois ciclismo
        vo2 = None
        for item in perf:
            v = item.get("generic", {}).get("vo2MaxPreciseValue") or \
                item.get("cycling", {}).get("vo2MaxPreciseValue")
            if v:
                vo2 = v
                break
        dados["vo2max"] = vo2
    except Exception:
        dados["vo2max"] = None

    # --- Peso ---
    try:
        pesos = api.get_weigh_ins(TODAY_STR, TODAY_STR)
        registros = pesos.get("dailyWeightSummaries", [])
        if registros:
            ultimo = registros[-1].get("allDayAvgWeightValue")  # gramas
            dados["peso"] = round(ultimo / 1000, 1) if ultimo else None
        else:
            dados["peso"] = None
    except Exception:
        dados["peso"] = None

    # --- Próxima prova (Race Predictor) ---
    try:
        races = api.get_race_predictions()
        # Tenta pegar 5K, 10K, meia e maratona
        distancias = {
            "5K":        races.get("time5K"),
            "10K":       races.get("time10K"),
            "Meia":      races.get("timeHalfMarathon"),
            "Maratona":  races.get("timeMarathon"),
        }
        dados["previsoes"] = {k: v for k, v in distancias.items() if v}
    except Exception:
        dados["previsoes"] = {}

    return dados

# ─── Formatação do briefing ───────────────────────────────────────────────────

def segundos_para_tempo(s):
    if not s:
        return "—"
    h, r = divmod(int(s), 3600)
    m, seg = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m{seg:02d}s"
    return f"{m}m{seg:02d}s"

def gerar_briefing_html(d):
    dia_semana = TODAY.strftime("%A")
    dias_pt = {"Monday":"Segunda","Tuesday":"Terça","Wednesday":"Quarta",
               "Thursday":"Quinta","Friday":"Sexta","Saturday":"Sábado","Sunday":"Domingo"}
    dia_str = f"{dias_pt.get(dia_semana, dia_semana)}, {TODAY.strftime('%d/%m/%Y')}"

    # ── Semáforos ──
    s_hrv      = semaforo(d["hrv"] or 0, (40, 60)) if d["hrv"] else "⚪"
    s_sono     = semaforo(d["sono_score"] or 0, (60, 80)) if d["sono_score"] else "⚪"
    s_ready    = semaforo(d["readiness"] or 0, (40, 70)) if d["readiness"] else "⚪"
    s_acwr     = semaforo(d["acwr"] or 0, (0.8, 1.3), inverso=False) if d["acwr"] else "⚪"
    # ACWR ideal: 0.8–1.3. Acima de 1.5 = sobrecarga.
    if d["acwr"] and d["acwr"] > 1.5:
        s_acwr = "🔴"
    elif d["acwr"] and 0.8 <= d["acwr"] <= 1.3:
        s_acwr = "🟢"
    elif d["acwr"]:
        s_acwr = "🟡"

    # ── Ação do dia ──
    acoes = []
    if d["readiness"] is not None:
        if d["readiness"] < 40:
            acoes.append("Recuperação ativa ou descanso total")
        elif d["readiness"] < 70:
            acoes.append("Treino leve a moderado")
        else:
            acoes.append("Dia ideal para treino de qualidade")
    if d["acwr"] and d["acwr"] > 1.5:
        acoes.append("⚠️ Carga elevada — reduza intensidade hoje")
    if d["sono_horas"] and d["sono_horas"] < 6:
        acoes.append("⚠️ Sono insuficiente — priorize recuperação")

    acao_dia = " · ".join(acoes) if acoes else "Siga seu plano normalmente"

    # ── Previsões de prova ──
    previsoes_html = ""
    if d["previsoes"]:
        linhas = "".join(
            f"<tr><td style='padding:4px 12px 4px 0;color:#888'>{k}</td>"
            f"<td style='padding:4px 0;font-weight:600'>{segundos_para_tempo(v)}</td></tr>"
            for k, v in d["previsoes"].items()
        )
        previsoes_html = f"""
        <tr><td colspan='2' style='padding-top:20px'>
          <div style='font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#aaa;margin-bottom:8px'>Previsão de Prova</div>
          <table style='border-collapse:collapse'>{linhas}</table>
        </td></tr>"""

    html = f"""<!DOCTYPE html>
<html lang='pt-BR'>
<head><meta charset='UTF-8'>
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background:#0f0f0f; color:#e8e8e8; margin:0; padding:0; }}
  .wrap {{ max-width:520px; margin:32px auto; background:#181818; border-radius:16px; overflow:hidden; }}
  .header {{ background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%); padding:32px 32px 24px; }}
  .header h1 {{ margin:0 0 4px; font-size:22px; font-weight:700; color:#fff; letter-spacing:-.02em; }}
  .header p  {{ margin:0; font-size:13px; color:#7090b0; }}
  .body  {{ padding:28px 32px 32px; }}
  .section-label {{ font-size:10px; text-transform:uppercase; letter-spacing:.1em; color:#555; margin-bottom:12px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:24px; }}
  .card {{ background:#222; border-radius:10px; padding:14px 16px; }}
  .card .icon {{ font-size:20px; margin-bottom:6px; }}
  .card .label {{ font-size:10px; color:#666; text-transform:uppercase; letter-spacing:.07em; margin-bottom:4px; }}
  .card .value {{ font-size:18px; font-weight:700; color:#f0f0f0; }}
  .card .sub   {{ font-size:11px; color:#888; margin-top:2px; }}
  .semaforo-row {{ display:flex; gap:12px; margin-bottom:24px; }}
  .sem {{ flex:1; background:#222; border-radius:10px; padding:12px 14px; text-align:center; }}
  .sem .em {{ font-size:24px; }}
  .sem .lb {{ font-size:10px; color:#666; text-transform:uppercase; letter-spacing:.07em; margin-top:4px; }}
  .acao {{ background:linear-gradient(135deg,#0f3460,#1a1a2e); border-radius:12px; padding:18px 20px; margin-top:4px; }}
  .acao .title {{ font-size:10px; text-transform:uppercase; letter-spacing:.1em; color:#7090b0; margin-bottom:6px; }}
  .acao .text  {{ font-size:15px; font-weight:600; color:#fff; line-height:1.4; }}
  .footer {{ text-align:center; padding:16px; font-size:11px; color:#444; border-top:1px solid #252525; }}
</style>
</head>
<body>
<div class='wrap'>
  <div class='header'>
    <h1>⌚ Briefing Garmin</h1>
    <p>{dia_str}</p>
  </div>
  <div class='body'>

    <div class='section-label'>Recuperação</div>
    <div class='grid'>
      <div class='card'>
        <div class='icon'>💤</div>
        <div class='label'>Sono</div>
        <div class='value'>{fmt(d["sono_horas"])}h</div>
        <div class='sub'>Score {fmt(d["sono_score"], 0)} · Deep {fmt(d["sono_deep_h"])}h</div>
      </div>
      <div class='card'>
        <div class='icon'>❤️</div>
        <div class='label'>HRV</div>
        <div class='value'>{fmt(d["hrv"], 0)} ms</div>
        <div class='sub'>{(d["hrv_status"] or "—").capitalize()}</div>
      </div>
      <div class='card'>
        <div class='icon'>🔋</div>
        <div class='label'>Readiness</div>
        <div class='value'>{fmt(d["readiness"], 0)}</div>
        <div class='sub'>Body Battery</div>
      </div>
    </div>

    <div class='section-label'>Carga &amp; Performance</div>
    <div class='grid'>
      <div class='card'>
        <div class='icon'>📈</div>
        <div class='label'>ACWR</div>
        <div class='value'>{fmt(d["acwr"], 2)}</div>
        <div class='sub'>Ideal: 0.8–1.3</div>
      </div>
      <div class='card'>
        <div class='icon'>🫁</div>
        <div class='label'>VO₂max</div>
        <div class='value'>{fmt(d["vo2max"], 1)}</div>
        <div class='sub'>ml/kg/min</div>
      </div>
      <div class='card'>
        <div class='icon'>⚖️</div>
        <div class='label'>Peso</div>
        <div class='value'>{fmt(d["peso"])} kg</div>
        <div class='sub'>{TODAY.strftime("%d/%m")}</div>
      </div>
    </div>

    <div class='section-label'>Semáforos</div>
    <div class='semaforo-row'>
      <div class='sem'><div class='em'>{s_ready}</div><div class='lb'>Recuperação</div></div>
      <div class='sem'><div class='em'>{s_acwr}</div><div class='lb'>Carga</div></div>
      <div class='sem'><div class='em'>{s_hrv}</div><div class='lb'>HRV</div></div>
      <div class='sem'><div class='em'>{s_sono}</div><div class='lb'>Sono</div></div>
    </div>

    <table style='width:100%'>{previsoes_html}</table>

    <div class='acao'>
      <div class='title'>🎯 Ação do Dia</div>
      <div class='text'>{acao_dia}</div>
    </div>

  </div>
  <div class='footer'>Gerado automaticamente via Garmin Connect · {TODAY_STR}</div>
</div>
</body></html>"""

    return html

# ─── Envio via SendGrid ───────────────────────────────────────────────────────

def enviar_email(html_content):
    import urllib.request
    payload = json.dumps({
        "personalizations": [{"to": [{"email": EMAIL_TO}]}],
        "from": {"email": EMAIL_FROM, "name": "Garmin Briefing"},
        "subject": f"⌚ Briefing Garmin — {TODAY.strftime('%d/%m/%Y')}",
        "content": [{"type": "text/html", "value": html_content}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        status = resp.status
    print(f"E-mail enviado — status {status}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[{TODAY_STR}] Coletando dados Garmin...")
    try:
        dados = coletar_dados()
    except Exception as e:
        print("Erro ao coletar dados:")
        traceback.print_exc()
        raise

    print("Dados coletados:", json.dumps(dados, default=str, ensure_ascii=False, indent=2))

    html = gerar_briefing_html(dados)
    enviar_email(html)
    print("Concluído.")

if __name__ == "__main__":
    main()
