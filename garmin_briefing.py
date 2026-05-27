#!/usr/bin/env python3
"""
Garmin Triathlon Briefing v2
- Coleta dados de saúde + treinos do dia anterior (natação/bike/corrida)
- Verifica treino agendado no calendário Garmin para hoje
- Usa Claude AI para gerar insights personalizados e validar se o treino é adequado
- Envia e-mail HTML completo via SendGrid
"""

import os
import json
import datetime
import traceback
import urllib.request
import urllib.error
import pickle
import pathlib

from garminconnect import Garmin

# ─── Config ───────────────────────────────────────────────────────────────────
GARMIN_EMAIL     = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD  = os.environ["GARMIN_PASSWORD"]
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
ANTHROPIC_API_KEY= os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM       = os.environ["EMAIL_FROM"]
EMAIL_TO         = os.environ["EMAIL_TO"]

# Cache de sessão — evita login repetido e o 429 do Garmin
SESSION_FILE = pathlib.Path("/tmp/garmin_session.pkl")

TODAY     = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
TODAY_STR     = TODAY.isoformat()
YESTERDAY_STR = YESTERDAY.isoformat()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def fmt(v, casas=1, sufixo="", fallback="—"):
    if v is None: return fallback
    return f"{round(float(v), casas)}{sufixo}"

def segundos_para_tempo(s):
    if not s: return "—"
    h, r = divmod(int(s), 3600)
    m, seg = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{seg:02d}s"

def metros_para_dist(m, modalidade="corrida"):
    if not m: return "—"
    km = m / 1000
    if modalidade == "natacao":
        return f"{int(m)}m" if km < 1 else f"{km:.1f}km"
    return f"{km:.1f}km"

def pace_corrida(segundos, metros):
    """Retorna pace em min/km"""
    if not segundos or not metros or metros == 0: return "—"
    pace_s = (segundos / metros) * 1000
    m, s = divmod(int(pace_s), 60)
    return f"{m}:{s:02d}/km"

def velocidade_bike(segundos, metros):
    if not segundos or not metros or segundos == 0: return "—"
    kmh = (metros / segundos) * 3600
    return f"{kmh:.1f}km/h"

def semaforo_emoji(valor, baixo, alto, inverso=False):
    if valor is None: return "⚪"
    if inverso:
        return "🟢" if valor <= baixo else ("🟡" if valor <= alto else "🔴")
    return "🟢" if valor >= alto else ("🟡" if valor >= baixo else "🔴")

# ─── Coleta Garmin ────────────────────────────────────────────────────────────

def garmin_login():
    """Login com cache de sessão para evitar 429.
    Compatível com qualquer versão da garminconnect — não depende de .garth.
    """
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, "rb") as f:
                session_data = pickle.load(f)
            api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            api.sess.cookies.update(session_data.get("cookies", {}))
            if session_data.get("headers"):
                api.sess.headers.update(session_data["headers"])
            api.get_full_name()  # valida sessão com chamada leve
            print("  Sessão restaurada do cache.")
            return api
        except Exception as e:
            print(f"  Cache inválido/expirado ({e}), fazendo novo login...")
            SESSION_FILE.unlink(missing_ok=True)

    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()

    try:
        session_data = {
            "cookies": dict(api.sess.cookies),
            "headers": dict(api.sess.headers),
        }
        with open(SESSION_FILE, "wb") as f:
            pickle.dump(session_data, f)
        print("  Login realizado e sessão salva em cache.")
    except Exception as e:
        print(f"  Aviso: não salvou cache ({e})")

    return api

def coletar_dados():
    api = garmin_login()
    dados = {"saude": {}, "treinos_ontem": [], "calendario_hoje": []}

    # === SAÚDE ===
    s = dados["saude"]

    try:
        hrv = api.get_hrv_data(TODAY_STR)
        summ = hrv.get("hrvSummary", {})
        s["hrv"]        = summ.get("lastNight")
        s["hrv_status"] = summ.get("hrvStatus", "").lower()
    except: s["hrv"] = s["hrv_status"] = None

    try:
        sono = api.get_sleep_data(TODAY_STR)
        d = sono.get("dailySleepDTO", {})
        s["sono_h"]      = round((d.get("sleepTimeSeconds") or 0) / 3600, 1)
        s["sono_score"]  = d.get("sleepScores", {}).get("overall", {}).get("value")
        s["sono_deep_h"] = round((d.get("deepSleepSeconds") or 0) / 3600, 1)
        s["sono_rem_h"]  = round((d.get("remSleepSeconds") or 0) / 3600, 1)
    except: s["sono_h"] = s["sono_score"] = s["sono_deep_h"] = s["sono_rem_h"] = None

    try:
        bb = api.get_body_battery(TODAY_STR, TODAY_STR)
        vals = [x.get("charged") for x in (bb or []) if x.get("charged") is not None]
        s["readiness"] = max(vals) if vals else None
    except: s["readiness"] = None

    try:
        ts = api.get_training_status(TODAY_STR)
        lb = ts.get("trainingLoadBalance", {})
        s["acwr"]            = round(lb.get("acuteChronicWorkloadRatio") or 0, 2) or None
        s["training_status"] = ts.get("trainingStatus", {}).get("trainingStatus", "")
        s["training_readiness"] = ts.get("trainingReadiness", {}).get("score")
    except: s["acwr"] = s["training_status"] = s["training_readiness"] = None

    try:
        perf = api.get_max_metrics(TODAY_STR)
        for item in perf:
            v = item.get("generic", {}).get("vo2MaxPreciseValue") or \
                item.get("cycling", {}).get("vo2MaxPreciseValue")
            if v:
                s["vo2max"] = v
                break
        else: s["vo2max"] = None
    except: s["vo2max"] = None

    try:
        pesos = api.get_weigh_ins(TODAY_STR, TODAY_STR)
        reg = pesos.get("dailyWeightSummaries", [])
        ultimo = reg[-1].get("allDayAvgWeightValue") if reg else None
        s["peso"] = round(ultimo / 1000, 1) if ultimo else None
    except: s["peso"] = None

    try:
        races = api.get_race_predictions()
        s["previsoes"] = {
            "5K":      races.get("time5K"),
            "10K":     races.get("time10K"),
            "Meia":    races.get("timeHalfMarathon"),
            "Maratona":races.get("timeMarathon"),
        }
        s["previsoes"] = {k: v for k, v in s["previsoes"].items() if v}
    except: s["previsoes"] = {}

    # === TREINOS DE ONTEM ===
    try:
        atividades = api.get_activities_by_date(YESTERDAY_STR, YESTERDAY_STR)
        for a in (atividades or []):
            tipo_raw = (a.get("activityType", {}).get("typeKey") or "").lower()
            if "swim" in tipo_raw or "natacao" in tipo_raw or "pool" in tipo_raw:
                modalidade = "natacao"
                icone = "🏊"
            elif "cycling" in tipo_raw or "bike" in tipo_raw or "ride" in tipo_raw:
                modalidade = "bike"
                icone = "🚴"
            elif "running" in tipo_raw or "run" in tipo_raw:
                modalidade = "corrida"
                icone = "🏃"
            else:
                modalidade = "outro"
                icone = "⚡"

            dist  = a.get("distance")
            dur   = a.get("duration")
            fc    = a.get("averageHR")
            fc_max= a.get("maxHR")
            cal   = a.get("calories")
            cad   = a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute")
            potencia = a.get("avgPower")
            tss   = a.get("trainingStressScore")
            nome  = a.get("activityName", tipo_raw.capitalize())

            # Pace / velocidade
            if modalidade == "corrida":
                perf_str = pace_corrida(dur, dist)
                perf_label = "Pace"
            elif modalidade == "bike":
                perf_str = velocidade_bike(dur, dist)
                perf_label = "Velocidade"
            elif modalidade == "natacao":
                # Pace natação: min/100m
                if dur and dist and dist > 0:
                    p = (dur / dist) * 100
                    mm, ss = divmod(int(p), 60)
                    perf_str = f"{mm}:{ss:02d}/100m"
                else:
                    perf_str = "—"
                perf_label = "Pace"
            else:
                perf_str = "—"
                perf_label = "—"

            dados["treinos_ontem"].append({
                "modalidade": modalidade,
                "icone": icone,
                "nome": nome,
                "distancia": metros_para_dist(dist, modalidade),
                "duracao": segundos_para_tempo(dur),
                "fc_media": fc,
                "fc_max": fc_max,
                "calorias": cal,
                "cadencia": cad,
                "potencia": potencia,
                "tss": tss,
                "perf_label": perf_label,
                "perf_valor": perf_str,
                # dados brutos para a IA
                "_dist_m": dist,
                "_dur_s": dur,
                "_tss": tss,
            })
    except:
        traceback.print_exc()

    # === CALENDÁRIO DE HOJE ===
    try:
        # get_workout_schedule retorna lista de workouts do calendário para a semana
        cal = api.get_workout_schedule(TODAY_STR)
        for w in (cal or []):
            # Cada item pode ter estrutura ligeiramente diferente por versão da lib
            nome_w    = (w.get("workoutName") or w.get("title") or w.get("description") or "Treino")
            tipo_raw  = (w.get("sportType") or w.get("activityType") or "")
            if isinstance(tipo_raw, dict):
                tipo_w = (tipo_raw.get("sportTypeKey") or tipo_raw.get("typeKey") or "").lower()
            else:
                tipo_w = str(tipo_raw).lower()

            duracao_w = w.get("estimatedDurationInSecs") or w.get("duration")
            dist_w    = w.get("estimatedDistanceInMeters") or w.get("distance")

            if "swim" in tipo_w:                          icone_w = "🏊"
            elif "cycl" in tipo_w or "bike" in tipo_w:   icone_w = "🚴"
            elif "run" in tipo_w:                         icone_w = "🏃"
            else:                                          icone_w = "⚡"

            dados["calendario_hoje"].append({
                "icone":    icone_w,
                "nome":     nome_w,
                "tipo":     tipo_w,
                "duracao":  segundos_para_tempo(duracao_w),
                "distancia":metros_para_dist(dist_w),
                "_duracao_s": duracao_w,
                "_dist_m":    dist_w,
            })
    except:
        traceback.print_exc()

    return dados

# ─── Claude AI Insights ───────────────────────────────────────────────────────

def gerar_insights_ia(dados):
    s = dados["saude"]
    treinos = dados["treinos_ontem"]
    calendario = dados["calendario_hoje"]

    # Monta contexto rico para a IA
    treinos_txt = ""
    for t in treinos:
        treinos_txt += f"\n- {t['modalidade'].upper()}: {t['distancia']}, {t['duracao']}, FC média {t['fc_media']}bpm, FC max {t['fc_max']}bpm"
        if t['potencia']: treinos_txt += f", Potência {t['potencia']}W"
        if t['tss']: treinos_txt += f", TSS {t['tss']}"
        if t['perf_valor'] != '—': treinos_txt += f", {t['perf_label']} {t['perf_valor']}"
    if not treinos_txt: treinos_txt = "\n- Nenhum treino registrado"

    cal_txt = ""
    for c in calendario:
        cal_txt += f"\n- {c['tipo'].upper()}: {c['nome']}, duração estimada {c['duracao']}, distância {c['distancia']}"
    if not cal_txt: cal_txt = "\n- Nenhum treino agendado para hoje"

    prompt = f"""Você é um treinador especialista em triathlon 70.3 com profundo conhecimento em fisiologia do esporte, periodização e análise de dados de wearables Garmin.

DADOS DE HOJE ({TODAY_STR}):
- HRV: {s.get('hrv')} ms ({s.get('hrv_status')})
- Sono: {s.get('sono_h')}h — Score {s.get('sono_score')} — Deep {s.get('sono_deep_h')}h — REM {s.get('sono_rem_h')}h
- Body Battery (Readiness): {s.get('readiness')}/100
- ACWR: {s.get('acwr')} (ideal 0.8–1.3)
- Training Readiness Garmin: {s.get('training_readiness')}
- VO2max: {s.get('vo2max')} ml/kg/min
- Status de treino Garmin: {s.get('training_status')}

TREINOS DE ONTEM ({YESTERDAY_STR}):{treinos_txt}

TREINO AGENDADO PARA HOJE NO CALENDÁRIO GARMIN:{cal_txt}

Responda em JSON com exatamente estas chaves (sem markdown, sem texto fora do JSON):
{{
  "resumo_ontem": "2-3 frases analisando os treinos de ontem: qualidade, execução, pontos positivos e de atenção por modalidade",
  "analise_recuperacao": "1-2 frases sobre o estado de recuperação atual baseado nos dados fisiológicos",
  "validacao_treino_hoje": "🟢 IDEAL / 🟡 AJUSTAR / 🔴 SUBSTITUIR — seguido de 1-2 frases explicando se o treino agendado é adequado para o estado atual",
  "sugestao_ajuste": "Se precisar ajustar/substituir: descreva o ajuste concreto (ex: reduzir volume 30%, trocar por natação técnica, etc). Se estiver ideal, escreva 'Siga o plano como programado.'",
  "foco_tecnico": "1 dica técnica específica para a(s) modalidade(s) de hoje baseada nos dados recentes",
  "alerta": "Qualquer alerta importante de saúde ou carga. Se não houver, escreva null"
}}"""

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Anthropic API error {e.code}: {body}")
        return {
            "resumo_ontem": f"Erro ao consultar IA (HTTP {e.code}). Verifique o secret ANTHROPIC_API_KEY no GitHub.",
            "analise_recuperacao": "—", "validacao_treino_hoje": "—",
            "sugestao_ajuste": "—", "foco_tecnico": "—", "alerta": None
        }

    raw = result["content"][0]["text"].strip()
    # Remove blocos markdown caso o modelo os inclua
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"JSON inválido da IA: {raw[:200]}")
        return {
            "resumo_ontem": raw[:400],
            "analise_recuperacao": "—", "validacao_treino_hoje": "—",
            "sugestao_ajuste": "—", "foco_tecnico": "—", "alerta": None
        }

# ─── HTML do e-mail ───────────────────────────────────────────────────────────

def gerar_html(dados, insights):
    s  = dados["saude"]
    tr = dados["treinos_ontem"]
    ca = dados["calendario_hoje"]

    dias_pt = {"Monday":"Segunda","Tuesday":"Terça","Wednesday":"Quarta",
               "Thursday":"Quinta","Friday":"Sexta","Saturday":"Sábado","Sunday":"Domingo"}
    dia_str = f"{dias_pt.get(TODAY.strftime('%A'), TODAY.strftime('%A'))}, {TODAY.strftime('%d/%m/%Y')}"

    # Semáforos
    s_ready = semaforo_emoji(s.get("readiness"), 40, 70)
    s_sono  = semaforo_emoji(s.get("sono_score"), 60, 80)
    s_hrv   = semaforo_emoji(s.get("hrv"), 40, 60)
    acwr    = s.get("acwr")
    if acwr is None:           s_acwr = "⚪"
    elif acwr > 1.5:           s_acwr = "🔴"
    elif 0.8 <= acwr <= 1.3:   s_acwr = "🟢"
    else:                      s_acwr = "🟡"

    # Treinos de ontem
    treinos_html = ""
    for t in tr:
        pot_html = f"<span class='tag'>⚡ {t['potencia']}W</span>" if t['potencia'] else ""
        tss_html = f"<span class='tag'>TSS {t['tss']}</span>" if t['tss'] else ""
        treinos_html += f"""
        <div class='treino-card'>
          <div class='treino-header'>
            <span class='treino-icon'>{t['icone']}</span>
            <span class='treino-titulo'>{t['nome']}</span>
          </div>
          <div class='treino-stats'>
            <div class='tstat'><div class='tstat-v'>{t['distancia']}</div><div class='tstat-l'>Distância</div></div>
            <div class='tstat'><div class='tstat-v'>{t['duracao']}</div><div class='tstat-l'>Duração</div></div>
            <div class='tstat'><div class='tstat-v'>{fmt(t['fc_media'], 0)} bpm</div><div class='tstat-l'>FC Média</div></div>
            <div class='tstat'><div class='tstat-v'>{t['perf_valor']}</div><div class='tstat-l'>{t['perf_label']}</div></div>
          </div>
          <div class='treino-tags'>{pot_html}{tss_html}<span class='tag'>🔥 {fmt(t['calorias'],0)} kcal</span></div>
        </div>"""

    if not treinos_html:
        treinos_html = "<p style='color:#555;font-size:13px;padding:12px 0'>Nenhum treino registrado ontem.</p>"

    # Calendário de hoje
    cal_html = ""
    for c in ca:
        cal_html += f"""
        <div class='cal-card'>
          <span class='cal-icon'>{c['icone']}</span>
          <div class='cal-info'>
            <div class='cal-nome'>{c['nome']}</div>
            <div class='cal-sub'>{c['duracao']} · {c['distancia']}</div>
          </div>
        </div>"""
    if not cal_html:
        cal_html = "<p style='color:#555;font-size:13px;padding:8px 0'>Nenhum treino agendado para hoje.</p>"

    # Alerta
    alerta_html = ""
    if insights.get("alerta"):
        alerta_html = f"""
        <div class='alerta-box'>
          <span style='font-size:18px'>⚠️</span>
          <div>{insights['alerta']}</div>
        </div>"""

    # Previsões
    prev_html = ""
    if s.get("previsoes"):
        itens = "".join(
            f"<div class='prev-item'><div class='prev-d'>{k}</div><div class='prev-t'>{segundos_para_tempo(v)}</div></div>"
            for k, v in s["previsoes"].items()
        )
        prev_html = f"""
        <div class='section-label'>🏁 Previsão de Prova</div>
        <div class='prev-grid'>{itens}</div>"""

    html = f"""<!DOCTYPE html>
<html lang='pt-BR'>
<head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600;700&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'DM Sans',sans-serif;background:#080c10;color:#d4dce8;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:560px;margin:24px auto;background:#0d1117;border-radius:20px;overflow:hidden;border:1px solid #1c2330}}
  .header{{background:linear-gradient(135deg,#0a1628 0%,#0d2240 40%,#0a1e35 100%);padding:28px 28px 22px;border-bottom:1px solid #1c2330;position:relative;overflow:hidden}}
  .header::before{{content:'';position:absolute;top:-40px;right:-40px;width:180px;height:180px;background:radial-gradient(circle,rgba(0,150,255,.12) 0%,transparent 70%);border-radius:50%}}
  .header-top{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}
  .header-badge{{background:rgba(0,150,255,.15);border:1px solid rgba(0,150,255,.3);border-radius:8px;padding:6px 10px;font-size:11px;font-family:'DM Mono',monospace;color:#60a5fa;letter-spacing:.05em}}
  .header h1{{font-size:20px;font-weight:700;color:#fff;letter-spacing:-.02em}}
  .header p{{font-size:12px;color:#4a6080;margin-top:3px;font-family:'DM Mono',monospace}}
  .body{{padding:24px 28px}}
  .section-label{{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:#3a5070;margin-bottom:12px;margin-top:24px;font-family:'DM Mono',monospace}}
  .section-label:first-child{{margin-top:0}}
  /* Semáforos */
  .sem-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:4px}}
  .sem{{background:#111827;border:1px solid #1c2330;border-radius:12px;padding:12px 8px;text-align:center}}
  .sem .em{{font-size:22px}}
  .sem .lb{{font-size:9px;color:#3a5070;text-transform:uppercase;letter-spacing:.08em;margin-top:5px;font-family:'DM Mono',monospace}}
  /* Grid saúde */
  .health-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}}
  .hcard{{background:#111827;border:1px solid #1c2330;border-radius:12px;padding:14px}}
  .hcard .hi{{font-size:16px;margin-bottom:6px}}
  .hcard .hl{{font-size:9px;color:#3a5070;text-transform:uppercase;letter-spacing:.08em;font-family:'DM Mono',monospace}}
  .hcard .hv{{font-size:17px;font-weight:700;color:#e8f0fe;margin:3px 0 1px;font-family:'DM Mono',monospace}}
  .hcard .hs{{font-size:10px;color:#4a6080}}
  /* Treinos */
  .treino-card{{background:#111827;border:1px solid #1c2330;border-radius:12px;padding:14px;margin-bottom:8px}}
  .treino-header{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
  .treino-icon{{font-size:18px}}
  .treino-titulo{{font-size:13px;font-weight:600;color:#c8d8f0}}
  .treino-stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px}}
  .tstat .tstat-v{{font-size:13px;font-weight:600;color:#e8f0fe;font-family:'DM Mono',monospace}}
  .tstat .tstat-l{{font-size:9px;color:#3a5070;text-transform:uppercase;letter-spacing:.06em;margin-top:2px}}
  .treino-tags{{display:flex;gap:6px;flex-wrap:wrap}}
  .tag{{background:#1c2330;border-radius:6px;padding:3px 8px;font-size:10px;color:#4a7090;font-family:'DM Mono',monospace}}
  /* Calendário */
  .cal-card{{background:#0f1923;border:1px solid #1a2a3a;border-left:3px solid #0066cc;border-radius:10px;padding:12px 14px;margin-bottom:8px;display:flex;align-items:center;gap:12px}}
  .cal-icon{{font-size:20px}}
  .cal-nome{{font-size:13px;font-weight:600;color:#c8d8f0}}
  .cal-sub{{font-size:11px;color:#4a6080;margin-top:2px;font-family:'DM Mono',monospace}}
  /* IA Insights */
  .ia-box{{background:#0a1628;border:1px solid #1a3050;border-radius:14px;padding:18px;margin-bottom:10px}}
  .ia-label{{font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#2a5080;font-family:'DM Mono',monospace;margin-bottom:6px}}
  .ia-text{{font-size:13px;color:#a0b8d0;line-height:1.6}}
  .ia-validacao{{background:#0a1e10;border:1px solid #1a4030;border-radius:14px;padding:16px;margin-bottom:10px}}
  .ia-validacao .ia-text{{font-size:14px;font-weight:500;color:#c8e8d0}}
  .ia-foco{{background:#1a1000;border:1px solid #3a2800;border-radius:14px;padding:14px;margin-bottom:10px}}
  .ia-foco .ia-text{{color:#d4b060}}
  /* Alerta */
  .alerta-box{{background:#1a0a0a;border:1px solid #5a1a1a;border-radius:12px;padding:14px 16px;display:flex;gap:10px;align-items:flex-start;margin-bottom:10px;font-size:13px;color:#e07070;line-height:1.5}}
  /* Previsões */
  .prev-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
  .prev-item{{background:#111827;border:1px solid #1c2330;border-radius:10px;padding:10px;text-align:center}}
  .prev-d{{font-size:9px;color:#3a5070;text-transform:uppercase;letter-spacing:.08em;font-family:'DM Mono',monospace}}
  .prev-t{{font-size:13px;font-weight:700;color:#e8f0fe;margin-top:4px;font-family:'DM Mono',monospace}}
  .footer{{text-align:center;padding:16px;font-size:10px;color:#1c2e40;border-top:1px solid #111827;font-family:'DM Mono',monospace}}
  .divider{{height:1px;background:#111827;margin:20px 0}}
</style>
</head>
<body>
<div class='wrap'>
  <div class='header'>
    <div class='header-top'>
      <div class='header-badge'>TRIATHLON 70.3</div>
    </div>
    <h1>⌚ Briefing Diário</h1>
    <p>{dia_str}</p>
  </div>
  <div class='body'>

    <!-- SEMÁFOROS -->
    <div class='section-label'>Status Geral</div>
    <div class='sem-row'>
      <div class='sem'><div class='em'>{s_ready}</div><div class='lb'>Readiness</div></div>
      <div class='sem'><div class='em'>{s_acwr}</div><div class='lb'>Carga</div></div>
      <div class='sem'><div class='em'>{s_hrv}</div><div class='lb'>HRV</div></div>
      <div class='sem'><div class='em'>{s_sono}</div><div class='lb'>Sono</div></div>
    </div>

    <!-- SAÚDE -->
    <div class='section-label' style='margin-top:20px'>Dados Fisiológicos</div>
    <div class='health-grid'>
      <div class='hcard'><div class='hi'>💤</div><div class='hl'>Sono</div><div class='hv'>{fmt(s.get('sono_h'))}h</div><div class='hs'>Score {fmt(s.get('sono_score'),0)} · Deep {fmt(s.get('sono_deep_h'))}h</div></div>
      <div class='hcard'><div class='hi'>❤️</div><div class='hl'>HRV</div><div class='hv'>{fmt(s.get('hrv'),0)}ms</div><div class='hs'>{(s.get('hrv_status') or '—').capitalize()}</div></div>
      <div class='hcard'><div class='hi'>🔋</div><div class='hl'>Readiness</div><div class='hv'>{fmt(s.get('readiness'),0)}</div><div class='hs'>Body Battery</div></div>
      <div class='hcard'><div class='hi'>📈</div><div class='hl'>ACWR</div><div class='hv'>{fmt(s.get('acwr'),2)}</div><div class='hs'>Ideal 0.8–1.3</div></div>
      <div class='hcard'><div class='hi'>🫁</div><div class='hl'>VO₂max</div><div class='hv'>{fmt(s.get('vo2max'),1)}</div><div class='hs'>ml/kg/min</div></div>
      <div class='hcard'><div class='hi'>⚖️</div><div class='hl'>Peso</div><div class='hv'>{fmt(s.get('peso'))}kg</div><div class='hs'>{TODAY.strftime('%d/%m')}</div></div>
    </div>

    <div class='divider'></div>

    <!-- TREINOS ONTEM -->
    <div class='section-label'>Treinos de Ontem</div>
    {treinos_html}

    <!-- IA: ANÁLISE ONTEM -->
    <div class='ia-box'>
      <div class='ia-label'>🤖 Análise IA — Execução dos treinos</div>
      <div class='ia-text'>{insights.get('resumo_ontem','—')}</div>
    </div>

    <div class='ia-box' style='margin-top:0'>
      <div class='ia-label'>💊 Análise IA — Recuperação atual</div>
      <div class='ia-text'>{insights.get('analise_recuperacao','—')}</div>
    </div>

    <div class='divider'></div>

    <!-- CALENDÁRIO HOJE -->
    <div class='section-label'>Treino Agendado para Hoje</div>
    {cal_html}

    <!-- IA: VALIDAÇÃO -->
    <div class='ia-validacao'>
      <div class='ia-label'>🤖 IA — Este treino está adequado?</div>
      <div class='ia-text'>{insights.get('validacao_treino_hoje','—')}</div>
    </div>

    <div class='ia-box' style='margin-top:0'>
      <div class='ia-label'>🔧 Sugestão de ajuste</div>
      <div class='ia-text'>{insights.get('sugestao_ajuste','—')}</div>
    </div>

    <div class='ia-foco'>
      <div class='ia-label'>🎯 Foco técnico de hoje</div>
      <div class='ia-text'>{insights.get('foco_tecnico','—')}</div>
    </div>

    {alerta_html}

    {prev_html}

  </div>
  <div class='footer'>Gerado por IA · Garmin Connect · {TODAY_STR}</div>
</div>
</body></html>"""

    return html

# ─── Envio SendGrid ───────────────────────────────────────────────────────────

def enviar_email(html):
    payload = json.dumps({
        "personalizations": [{"to": [{"email": EMAIL_TO}]}],
        "from": {"email": EMAIL_FROM, "name": "Triathlon Briefing IA"},
        "subject": f"⌚ Briefing Triathlon — {TODAY.strftime('%d/%m/%Y')}",
        "content": [{"type": "text/html", "value": html}]
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
    with urllib.request.urlopen(req) as r:
        print(f"E-mail enviado — HTTP {r.status}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[{TODAY_STR}] Coletando dados Garmin...")
    dados = coletar_dados()
    print(f"  Treinos ontem: {len(dados['treinos_ontem'])}")
    print(f"  Calendário hoje: {len(dados['calendario_hoje'])}")

    print("Gerando insights com Claude AI...")
    insights = gerar_insights_ia(dados)
    print("Insights:", json.dumps(insights, ensure_ascii=False, indent=2))

    html = gerar_html(dados, insights)
    enviar_email(html)
    print("Concluído ✅")

if __name__ == "__main__":
    main()
