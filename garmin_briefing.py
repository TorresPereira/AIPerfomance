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

SESSION_FILE = pathlib.Path("/tmp/garmin_session.pkl")

# ─── Config ───────────────────────────────────────────────────────────────────
GARMIN_EMAIL     = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD  = os.environ["GARMIN_PASSWORD"]
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
ANTHROPIC_API_KEY= os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM       = os.environ["EMAIL_FROM"]
EMAIL_TO         = os.environ["EMAIL_TO"]

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
    """Login usando garth (novo padrão da lib) com cache de sessão."""
    import os
    cache_dir = "/tmp/garmin_cache"
    os.makedirs(cache_dir, exist_ok=True)
    try:
        api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        api.login(tokenstore=cache_dir)
        print("  Sessão restaurada do cache.")
        return api
    except Exception:
        pass
    try:
        api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        api.login()
        try:
            api.garth.dump(cache_dir)
            print("  Login novo, sessão salva via garth.")
        except Exception as e:
            print(f"  Login novo (sem cache garth: {e})")
        return api
    except Exception as e:
        print(f"  Login sem cache: {e}")
        api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        api.login()
        return api

def coletar_dados():
    api = garmin_login()
    dados = {"saude": {}, "treinos_ontem": [], "calendario_hoje": []}

    # === SAÚDE ===
    s = dados["saude"]

    # === HRV ===
    try:
        hrv_raw = api.get_hrv_data(TODAY_STR)
        print("  [DEBUG HRV raw]:", json.dumps(hrv_raw, default=str)[:500])
        summ = hrv_raw.get("hrvSummary", {})
        # Tenta lastNight primeiro, depois weeklyAvg, depois valor direto
        s["hrv"] = (summ.get("lastNight")
                    or summ.get("lastNight5MinHigh")
                    or hrv_raw.get("hrvSummary", {}).get("lastNight"))
        s["hrv_status"] = (summ.get("hrvStatus") or summ.get("status") or "").lower()
        if not s["hrv"]:
            # Tenta pegar do array de leituras se existir
            readings = hrv_raw.get("hrv5MinReadings") or hrv_raw.get("hrvReadings") or []
            if readings:
                vals = [r.get("hrvValue") or r.get("value") for r in readings if r]
                vals = [v for v in vals if v]
                s["hrv"] = round(sum(vals)/len(vals), 1) if vals else None
        print(f"  HRV extraído: {s['hrv']} ({s['hrv_status']})")
    except Exception as e:
        print(f"  [HRV ERROR] {e}")
        s["hrv"] = s["hrv_status"] = None

    # === SONO ===
    try:
        sono = api.get_sleep_data(TODAY_STR)
        d = sono.get("dailySleepDTO", {})
        s["sono_h"]      = round((d.get("sleepTimeSeconds") or 0) / 3600, 1)
        s["sono_score"]  = d.get("sleepScores", {}).get("overall", {}).get("value")
        s["sono_deep_h"] = round((d.get("deepSleepSeconds") or 0) / 3600, 1)
        s["sono_rem_h"]  = round((d.get("remSleepSeconds") or 0) / 3600, 1)
        print(f"  Sono: {s['sono_h']}h score={s['sono_score']}")
    except Exception as e:
        print(f"  [SONO ERROR] {e}")
        s["sono_h"] = s["sono_score"] = s["sono_deep_h"] = s["sono_rem_h"] = None

    # === BODY BATTERY ===
    try:
        bb = api.get_body_battery(TODAY_STR, TODAY_STR)
        vals = [x.get("charged") for x in (bb or []) if x.get("charged") is not None]
        s["readiness"] = max(vals) if vals else None
        print(f"  Readiness: {s['readiness']}")
    except Exception as e:
        print(f"  [READINESS ERROR] {e}")
        s["readiness"] = None

    # === TRAINING STATUS / ACWR ===
    try:
        ts = api.get_training_status(TODAY_STR)
        lb = ts.get("trainingLoadBalance", {})
        s["acwr"]               = round(lb.get("acuteChronicWorkloadRatio") or 0, 2) or None
        s["training_status"]    = ts.get("trainingStatus", {}).get("trainingStatus", "")
        s["training_readiness"] = ts.get("trainingReadiness", {}).get("score")
        print(f"  ACWR: {s['acwr']}")
    except Exception as e:
        print(f"  [ACWR ERROR] {e}")
        s["acwr"] = s["training_status"] = s["training_readiness"] = None

    # === VO2MAX ===
    try:
        s["vo2max"] = None
        def _extract_vo2(obj):
            if not obj: return None
            if isinstance(obj, list):
                for item in obj:
                    v = _extract_vo2(item)
                    if v: return v
                return None
            if isinstance(obj, dict):
                for key in ["vo2MaxPreciseValue","vo2Max","vo2MaxValue","currentVo2Max","latestVo2Max"]:
                    if obj.get(key): return obj[key]
                for sub in ["generic","cycling","running","swimming"]:
                    v = _extract_vo2(obj.get(sub))
                    if v: return v
            return None

        for attempt in [
            lambda: api.get_max_metrics(TODAY_STR),
            lambda: api.get_training_status(TODAY_STR),
            lambda: api.get_performance_metrics(TODAY_STR),
            lambda: api.get_user_summary(TODAY_STR),
            lambda: api.get_stats(TODAY_STR),
        ]:
            if s["vo2max"]: break
            try:
                result = attempt()
                s["vo2max"] = _extract_vo2(result)
            except Exception: pass

        print(f"  VO2max: {s['vo2max']}")
    except Exception as e:
        print(f"  [VO2MAX ERROR] {e}")
        s["vo2max"] = None

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
        # Tenta métodos alternativos para o calendário
        cal = None
        year, month = TODAY.year, TODAY.month
        attempts = [
            ("get_workout_schedule",    lambda: api.get_workout_schedule(TODAY_STR)),
            ("get_scheduled_workouts",  lambda: api.get_scheduled_workouts(year, month)),
            ("get_garmin_workouts",     lambda: api.get_garmin_workouts()),
            ("get_calendar_items",      lambda: api.get_calendar_items(TODAY_STR, TODAY_STR)),
        ]
        for name, fn in attempts:
            try:
                result = fn()
                # Filtra apenas treinos de hoje se retornou lista maior
                if isinstance(result, list):
                    today_items = [w for w in result if
                        (w.get("scheduledDate") or w.get("date") or w.get("calendarDate") or "") == TODAY_STR
                        or len(result) <= 5  # se poucos itens assume que são de hoje
                    ]
                    cal = today_items if today_items else result
                else:
                    cal = result
                print(f"  Calendário via {name}: {len(cal or [])} item(s)")
                break
            except Exception as em:
                print(f"  {name} falhou: {em}")
        for w in (cal or []):
            nome_w  = w.get("workoutName") or w.get("description") or "Treino"
            tipo_w  = (w.get("sportType", {}).get("sportTypeKey") or "").lower()
            duracao_w = w.get("estimatedDurationInSecs")
            dist_w    = w.get("estimatedDistanceInMeters")

            if "swim" in tipo_w: icone_w = "🏊"
            elif "cycling" in tipo_w or "bike" in tipo_w: icone_w = "🚴"
            elif "running" in tipo_w or "run" in tipo_w: icone_w = "🏃"
            else: icone_w = "⚡"

            dados["calendario_hoje"].append({
                "icone": icone_w,
                "nome": nome_w,
                "tipo": tipo_w,
                "duracao": segundos_para_tempo(duracao_w),
                "distancia": metros_para_dist(dist_w),
                "_duracao_s": duracao_w,
                "_dist_m": dist_w,
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
  "frase_motivacional": "1 frase motivacional curta e poderosa para triatleta 70.3, personalizada para o estado atual. Máximo 15 palavras. Em português. Sem clichês.",
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
        return {"frase_motivacional": "Foco no processo.", "resumo_ontem": f"Erro IA (HTTP {e.code}). Verifique ANTHROPIC_API_KEY.",
                "analise_recuperacao": "—", "validacao_treino_hoje": "—", "sugestao_ajuste": "—", "foco_tecnico": "—", "alerta": None}

    raw = result["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"JSON inválido: {raw[:200]}")
        return {"frase_motivacional": "Foco no processo.", "resumo_ontem": raw[:400],
                "analise_recuperacao": "—", "validacao_treino_hoje": "—", "sugestao_ajuste": "—", "foco_tecnico": "—", "alerta": None}

# ─── HTML do e-mail ───────────────────────────────────────────────────────────

def _sem_colors(v, lo, hi):
    e = semaforo_emoji(v, lo, hi)
    if e == "\U0001f7e2": return e, "#00C896", "#003a28"
    if e == "\U0001f7e1": return e, "#FFB800", "#3a2a00"
    if e == "\U0001f534": return e, "#FF4444", "#3a0000"
    return e, "#1c1c1c", "#555"

def gerar_html(dados, insights):
    s  = dados["saude"]
    tr = dados["treinos_ontem"]
    ca = dados["calendario_hoje"]

    dias_pt = {"Monday":"Segunda","Tuesday":"Tera","Wednesday":"Quarta",
               "Thursday":"Quinta","Friday":"Sexta","Saturday":"Sabado","Sunday":"Domingo"}
    dia_str = dias_pt.get(TODAY.strftime("%A"), TODAY.strftime("%A")) + ", " + TODAY.strftime("%d/%m/%Y")

    r_em, r_bg, r_dk = _sem_colors(s.get("readiness"), 40, 70)
    h_em, h_bg, h_dk = _sem_colors(s.get("hrv"), 40, 60)
    sn_em, sn_bg, sn_dk = _sem_colors(s.get("sono_score"), 60, 80)
    acwr = s.get("acwr")
    if acwr is None:          a_em,a_bg,a_dk = "\u26aa","#1c1c1c","#555"
    elif acwr > 1.5:          a_em,a_bg,a_dk = "\U0001f534","#FF4444","#3a0000"
    elif 0.8 <= acwr <= 1.3:  a_em,a_bg,a_dk = "\U0001f7e2","#00C896","#003a28"
    else:                     a_em,a_bg,a_dk = "\U0001f7e1","#FFB800","#3a2a00"

    frase = insights.get("frase_motivacional", "Cada treino e um tijolo na sua melhor versao.")

    def D(st, c): return '<div style="'+st+'">'+c+'</div>'
    def S(st, c): return '<span style="'+st+'">'+c+'</span>'

    def sembox(em, bg, dk, label):
        return D("background:"+bg+";border-radius:8px;padding:13px 4px;text-align:center",
            S("font-size:20px;display:block", em) +
            S("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:"+dk+";display:block;margin-top:5px", label))

    def mcard(icon, label, val, sub, has):
        bc = "#1a6fff" if has else "#242424"
        vc = "#ffffff" if has else "#333"
        return D("background:#181818;border-radius:8px;padding:13px 11px;border-left:3px solid "+bc,
            S("font-size:13px", icon) +
            D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444;margin:4px 0 2px", label) +
            D("font-size:19px;font-weight:700;color:"+vc+";line-height:1", val) +
            D("font-size:10px;color:#444;margin-top:2px", sub))

    def tcard(t):
        tags = ""
        if t["potencia"]: tags += S("background:#1e1e1e;border:1px solid #2a2a2a;border-radius:3px;padding:2px 7px;font-size:10px;color:#555;margin-right:4px", "&#9889; "+str(t["potencia"])+"W")
        if t["tss"]:      tags += S("background:#1e1e1e;border:1px solid #2a2a2a;border-radius:3px;padding:2px 7px;font-size:10px;color:#555;margin-right:4px", "TSS "+str(t["tss"]))
        tags += S("background:#1e1e1e;border:1px solid #2a2a2a;border-radius:3px;padding:2px 7px;font-size:10px;color:#555;margin-right:4px", "&#128293; "+fmt(t["calorias"],0)+" kcal")
        stats = "".join(D("",D("font-size:14px;font-weight:700;color:#fff;line-height:1",v)+D("font-size:8px;text-transform:uppercase;letter-spacing:.07em;color:#444;margin-top:2px",l)) for v,l in [(t["distancia"],"Distancia"),(t["duracao"],"Duracao"),(fmt(t["fc_media"],0)+" bpm","FC Media"),(t["perf_valor"],t["perf_label"])])
        return D("background:#181818;border-radius:8px;padding:14px;margin-bottom:8px;border-top:2px solid #1a6fff",
            D("display:flex;align-items:center;gap:8px;margin-bottom:10px", S("font-size:9px;font-weight:700;letter-spacing:.1em;background:#1a6fff;color:#fff;padding:3px 7px;border-radius:3px",t["icone"]+" "+t["modalidade"].upper())+S("font-size:12px;color:#555",t["nome"]))+
            D("display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px",stats)+
            D("display:flex;flex-wrap:wrap;gap:4px",tags))

    def ccard(c):
        return D("display:flex;align-items:center;gap:12px;background:#181818;border-radius:8px;padding:13px;margin-bottom:7px;border-left:3px solid #00C896",
            S("font-size:20px;min-width:28px;text-align:center",c["icone"])+D("",D("font-size:13px;font-weight:600;color:#fff",c["nome"])+D("font-size:11px;color:#444;margin-top:2px",c["duracao"]+" - "+c["distancia"])))

    def ia(label, text, bg="#07111f", bc="#1a6fff", lc="#1a6fff", tc="#7a9bbf"):
        return D("background:"+bg+";border-left:3px solid "+bc+";border-radius:8px;padding:13px 15px;margin-bottom:7px",
            D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:"+lc+";margin-bottom:5px",label)+
            D("font-size:13px;color:"+tc+";line-height:1.6",str(text)))

    treinos_html = "".join(tcard(t) for t in tr) if tr else D("color:#444;font-size:13px;padding:10px 0","Nenhum treino registrado ontem.")
    cal_html = "".join(ccard(c) for c in ca) if ca else D("color:#444;font-size:13px","Nenhum treino agendado.")

    alerta_html = ""
    if insights.get("alerta"):
        alerta_html = D("background:#110000;border-left:3px solid #FF4444;border-radius:8px;padding:13px 15px;font-size:13px;color:#cc5555;line-height:1.5;margin-bottom:8px","&#9888;&#65039; "+str(insights["alerta"]))

    prev_html = ""
    if s.get("previsoes"):
        pitems = "".join(D("background:#181818;border-radius:7px;padding:10px 6px;text-align:center",D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444",k)+D("font-size:15px;font-weight:700;color:#fff;margin-top:4px",segundos_para_tempo(v))) for k,v in s["previsoes"].items())
        prev_html = D("font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:#333;margin-bottom:10px;margin-top:22px","&#127937; Previsao de Prova")+D("display:grid;grid-template-columns:repeat(4,1fr);gap:6px",pitems)

    SL = "font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:#333;margin-bottom:10px"
    DIV = D("height:1px;background:#1e1e1e;margin:18px 0","")

    body = (
        D(SL,"Status Geral")+
        D("display:grid;grid-template-columns:repeat(4,1fr);gap:6px",sembox(r_em,r_bg,r_dk,"Readiness")+sembox(a_em,a_bg,a_dk,"Carga")+sembox(h_em,h_bg,h_dk,"HRV")+sembox(sn_em,sn_bg,sn_dk,"Sono"))+
        D(SL+";margin-top:22px","Dados Fisiologicos")+
        D("display:grid;grid-template-columns:repeat(3,1fr);gap:6px",
            mcard("&#128164;","Sono",fmt(s.get("sono_h"))+"h","Score "+fmt(s.get("sono_score"),0)+" - Deep "+fmt(s.get("sono_deep_h"))+"h",bool(s.get("sono_h")))+
            mcard("&#10084;&#65039;","HRV",fmt(s.get("hrv"),0)+" ms",(s.get("hrv_status") or "-").capitalize(),bool(s.get("hrv")))+
            mcard("&#128267;","Readiness",fmt(s.get("readiness"),0),"Body Battery",bool(s.get("readiness")))+
            mcard("&#128200;","ACWR",fmt(s.get("acwr"),2),"Ideal 0.8-1.3",bool(s.get("acwr")))+
            mcard("&#129755;","VO2max",fmt(s.get("vo2max"),1),"ml/kg/min",bool(s.get("vo2max")))+
            mcard("&#9878;&#65039;","Peso",fmt(s.get("peso"))+" kg",TODAY.strftime("%d/%m"),bool(s.get("peso"))))+
        DIV+
        D(SL,"Treinos de Ontem")+treinos_html+
        ia("&#129302; Analise dos treinos",insights.get("resumo_ontem","--"))+
        ia("&#128138; Recuperacao atual",insights.get("analise_recuperacao","--"))+
        DIV+
        D(SL,"Treino Agendado para Hoje")+cal_html+
        ia("&#129302; Este treino esta adequado?",insights.get("validacao_treino_hoje","--"),tc="#a8ccf0")+
        ia("&#128295; Sugestao de ajuste",insights.get("sugestao_ajuste","--"))+
        ia("&#127919; Foco tecnico de hoje",insights.get("foco_tecnico","--"),bg="#110d00",bc="#FFB800",lc="#FFB800",tc="#c09040")+
        alerta_html+prev_html
    )

    return (
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'></head>"
        "<body style='font-family:Arial,Helvetica,sans-serif;background:#050505;color:#e0e0e0;margin:0;padding:0'>"
        "<div style='max-width:560px;margin:20px auto;background:#0f0f0f;border-radius:14px;overflow:hidden;border:1px solid #222'>"
        "<div style='background:#000;border-bottom:3px solid #1a6fff'>"
        "<div style='padding:18px 24px 0;display:flex;justify-content:space-between;align-items:center'>"
        "<span style='font-size:10px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:#fff;background:#1a6fff;padding:4px 10px;border-radius:4px'>&#8987; Triathlon 70.3</span>"
        "<span style='font-size:11px;color:#444'>"+dia_str+"</span>"
        "</div>"
        "<div style='padding:16px 24px 20px;font-size:22px;font-weight:700;line-height:1.25;color:#fff'>"+frase+"</div>"
        "</div>"
        "<div style='padding:22px 24px 28px'>"+body+"</div>"
        "<div style='background:#000;text-align:center;padding:12px;font-size:9px;color:#2a2a2a;letter-spacing:.1em;text-transform:uppercase'>Gerado por IA - Garmin Connect - "+TODAY_STR+"</div>"
        "</div></body></html>"
    )


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
