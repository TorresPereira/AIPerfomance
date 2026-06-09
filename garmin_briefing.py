#!/usr/bin/env python3
"""Garmin Triathlon Briefing v3 — Coach técnico para Half Ironman."""

import os, json, datetime, traceback, urllib.request, urllib.error, pickle, pathlib
from garminconnect import Garmin

# ─── Config ──────────────────────────────────────────────────────────────────
GARMIN_EMAIL      = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD   = os.environ["GARMIN_PASSWORD"]
SENDGRID_API_KEY  = os.environ["SENDGRID_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_TO          = os.environ["EMAIL_TO"]

TODAY     = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
TOMORROW  = TODAY + datetime.timedelta(days=1)
TODAY_STR     = TODAY.isoformat()
YESTERDAY_STR = YESTERDAY.isoformat()
TOMORROW_STR  = TOMORROW.isoformat()
CACHE_DIR     = "/tmp/garmin_cache"

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fmt(v, d=1, s="", fb="—"):
    if v is None: return fb
    return f"{round(float(v),d)}{s}"

def hms(sec):
    if not sec: return "—"
    h,r = divmod(int(sec),3600); m,s = divmod(r,60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"

def dist_fmt(m, mod="run"):
    if not m: return "—"
    km = m/1000
    return f"{int(m)}m" if mod=="swim" and km<2 else f"{km:.1f}km"

def pace_run(sec,m):
    if not sec or not m or m==0: return "—"
    p=int((sec/m)*1000); mm,ss=divmod(p,60)
    return f"{mm}:{ss:02d}/km"

def pace_swim(sec,m):
    if not sec or not m or m==0: return "—"
    p=int((sec/m)*100); mm,ss=divmod(p,60)
    return f"{mm}:{ss:02d}/100m"

def spd_bike(sec,m):
    if not sec or not m or sec==0: return "—"
    return f"{(m/sec)*3.6:.1f}km/h"

def sem(v, lo, hi):
    if v is None: return "⚪","#1c1c1c","#555"
    if v>=hi:  return "🟢","#00C896","#003a28"
    if v>=lo:  return "🟡","#FFB800","#3a2a00"
    return "🔴","#FF4444","#3a0000"

# ─── Login Garmin ─────────────────────────────────────────────────────────────
def garmin_login():
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        api.login(tokenstore=CACHE_DIR)
        print("  Sessão restaurada.")
        return api
    except Exception: pass
    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()
    try: api.garth.dump(CACHE_DIR); print("  Login novo, cache salvo.")
    except Exception as e: print(f"  Login ok (sem cache: {e})")
    return api

# ─── Coleta de dados ──────────────────────────────────────────────────────────
def coletar():
    api = garmin_login()
    d = {"saude":{}, "ontem":[], "hoje":[], "amanha":[], "hoje_feito":[]}
    s = d["saude"]

    # HRV
    try:
        r = api.get_hrv_data(TODAY_STR)
        summ = r.get("hrvSummary",{})
        s["hrv"]        = summ.get("lastNight5MinHigh") or summ.get("lastNightAvg")
        s["hrv_7d"]     = summ.get("weeklyAvg")
        s["hrv_status"] = summ.get("status","").lower()
        s["hrv_baseline_low"]  = summ.get("baseline",{}).get("balancedLow")
        s["hrv_baseline_high"] = summ.get("baseline",{}).get("balancedUpper")
        print(f"  HRV: {s['hrv']} (7d avg {s['hrv_7d']}, status {s['hrv_status']})")
    except Exception as e: print(f"  HRV err: {e}"); s["hrv"]=s["hrv_7d"]=s["hrv_status"]=None

    # Resting HR — WELLNESS_RESTING_HEART_RATE confirmado no Garmin 965
    s["rhr"] = s["rhr_baseline"] = None
    try:
        hr = api.get_rhr_day(TODAY_STR)
        metrics = hr.get("allMetrics",{}).get("metricsMap",{})
        rhr_list = metrics.get("WELLNESS_RESTING_HEART_RATE",[])
        s["rhr"] = int(rhr_list[0]["value"]) if rhr_list else None
        if not s["rhr"]:
            s["rhr"] = hr.get("restingHeartRate")
        print(f"  RHR: {s['rhr']} bpm")
    except Exception as e:
        print(f"  RHR err: {e}")
        try:
            ud = api.get_user_summary(TODAY_STR)
            s["rhr"] = (ud.get("restingHeartRateInBeatsPerMinute")
                        or ud.get("minHeartRateInBeatsPerMinute"))
        except: pass


    # Sono
    try:
        r = api.get_sleep_data(TODAY_STR)
        dl = r.get("dailySleepDTO",{})
        s["sono_h"]     = round((dl.get("sleepTimeSeconds") or 0)/3600,1)
        s["sono_score"] = dl.get("sleepScores",{}).get("overall",{}).get("value")
        s["sono_deep"]  = round((dl.get("deepSleepSeconds") or 0)/3600,1)
        s["sono_rem"]   = round((dl.get("remSleepSeconds") or 0)/3600,1)
        s["sono_awake"] = round((dl.get("awakeSleepSeconds") or 0)/60,0)
        print(f"  Sono: {s['sono_h']}h score={s['sono_score']} deep={s['sono_deep']}h rem={s['sono_rem']}h")
    except Exception as e: print(f"  Sono err: {e}"); s["sono_h"]=s["sono_score"]=s["sono_deep"]=s["sono_rem"]=None

    # Body Battery / Stress
    try:
        bb = api.get_body_battery(TODAY_STR, TODAY_STR)
        vals = [x.get("charged") for x in (bb or []) if x.get("charged") is not None]
        s["body_battery"] = max(vals) if vals else None
        print(f"  Body Battery: {s['body_battery']}")
    except: s["body_battery"] = None

    try:
        ud = api.get_user_summary(TODAY_STR)
        s["stress_avg"] = ud.get("averageStressLevel")
        s["stress_rest"] = ud.get("restStressPercentage")
    except: s["stress_avg"]=s["stress_rest"]=None

    # Training Load — Garmin 965 usa mostRecentTrainingLoadBalance + mostRecentTrainingStatus
    try:
        ts = api.get_training_status(TODAY_STR)

        # Garmin 965: dados indexados por deviceId
        STATUS_LABELS = {0:"Sem dados",1:"Peaking",2:"Produtivo",3:"Mantendo",
                         4:"Recuperação",5:"Improdutivo",6:"Destreinando",7:"Sobrecarga"}
        TREND_LABELS  = {1:"Melhorando",2:"Estável",3:"Caindo"}

        # ── Training Load Balance ──
        lb_raw = ts.get("mostRecentTrainingLoadBalance") or {}
        lb_map = lb_raw.get("metricsTrainingLoadBalanceDTOMap") or {}
        lb = next(iter(lb_map.values()), {}) if lb_map else {}

        aerobic_low  = float(lb.get("monthlyLoadAerobicLow")  or 0)
        aerobic_high = float(lb.get("monthlyLoadAerobicHigh") or 0)
        anaerobic    = float(lb.get("monthlyLoadAnaerobic")    or 0)
        total_monthly = aerobic_low + aerobic_high + anaerobic

        # Targets para comparação
        target_low_min  = lb.get("monthlyLoadAerobicLowTargetMin")
        target_low_max  = lb.get("monthlyLoadAerobicLowTargetMax")
        target_high_min = lb.get("monthlyLoadAerobicHighTargetMin")
        target_high_max = lb.get("monthlyLoadAerobicHighTargetMax")

        # Aproximação ATL (carga semanal = mensal / 4) e CTL (mensal)
        s["atl"]     = round(total_monthly / 4, 0) if total_monthly else None
        s["ctl"]     = round(total_monthly, 0)     if total_monthly else None
        s["tsb"]     = None  # Garmin não expõe TSB diretamente
        s["acwr"]    = None  # idem
        s["load_3d"] = lb.get("weeklyLoadAerobicLow") or lb.get("sevenDayLoad")

        # Cargas por zona (para o prompt da IA)
        s["load_aerobic_low"]  = round(aerobic_low, 0)  if aerobic_low  else None
        s["load_aerobic_high"] = round(aerobic_high, 0) if aerobic_high else None
        s["load_anaerobic"]    = round(anaerobic, 0)    if anaerobic    else None
        s["load_total_month"]  = round(total_monthly,0) if total_monthly else None
        s["load_target_low"]   = f"{target_low_min}–{target_low_max}"  if target_low_min else None
        s["load_target_high"]  = f"{target_high_min}–{target_high_max}" if target_high_min else None

        # ── Training Status ──
        st_map = (ts.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        st = next(iter(st_map.values()), {}) if st_map else {}

        status_code  = st.get("trainingStatus")
        trend_code   = st.get("fitnessTrend")
        weekly_load  = st.get("weeklyTrainingLoad")

        s["training_status"]    = STATUS_LABELS.get(status_code, str(status_code)) if status_code is not None else None
        s["fitness_trend"]      = TREND_LABELS.get(trend_code, str(trend_code))    if trend_code is not None else None
        s["training_readiness"] = None  # 965 não tem Training Readiness score numérico
        if weekly_load: s["atl"] = round(weekly_load, 0)

        print(f"  Carga mensal: {total_monthly:.0f} (Z1={aerobic_low:.0f} Z2={aerobic_high:.0f} Z3+={anaerobic:.0f})")
        print(f"  ATL~={s['atl']} CTL~={s['ctl']} Status={s['training_status']} Trend={s['fitness_trend']}")
    except Exception as e:
        print(f"  Training load err: {e}")
        traceback.print_exc()
        s["atl"]=s["ctl"]=s["tsb"]=s["acwr"]=s["load_3d"]=s["training_status"]=s["training_readiness"]=None
        s["load_aerobic_low"]=s["load_aerobic_high"]=s["load_anaerobic"]=s["load_total_month"]=None
        s["load_target_low"]=s["load_target_high"]=s["fitness_trend"]=None

    # VO2max — campo confirmado: get_training_status → mostRecentVO2Max.generic.vo2MaxPreciseValue
    try:
        s["vo2max"] = None
        ts_vo2 = api.get_training_status(TODAY_STR)
        vo2_node = ts_vo2.get("mostRecentVO2Max") or {}
        # Tenta corrida primeiro, depois ciclismo
        for sport in ["generic", "running", "cycling"]:
            node = vo2_node.get(sport) or {}
            v = node.get("vo2MaxPreciseValue") or node.get("vo2MaxValue")
            if v:
                s["vo2max"] = v
                break
        print(f"  VO2max: {s['vo2max']}")
    except Exception as e:
        print(f"  VO2max err: {e}")
        s["vo2max"] = None

    # Peso
    try:
        pw = api.get_weigh_ins(TODAY_STR, TODAY_STR)
        reg = pw.get("dailyWeightSummaries",[])
        s["peso"] = round(reg[-1].get("allDayAvgWeightValue")/1000,1) if reg else None
    except: s["peso"]=None

    # Race predictions
    try:
        r = api.get_race_predictions()
        s["previsoes"] = {k:v for k,v in {"5K":r.get("time5K"),"10K":r.get("time10K"),"Meia":r.get("timeHalfMarathon"),"Maratona":r.get("timeMarathon")}.items() if v}
    except: s["previsoes"]={}

    # ─ Treinos de ontem ─
    try:
        acts = api.get_activities_by_date(YESTERDAY_STR, YESTERDAY_STR)
        for a in (acts or []):
            tipo = (a.get("activityType",{}).get("typeKey") or "").lower()
            if   "swim" in tipo or "pool" in tipo: mod,ico = "swim","🏊"
            elif "cycl" in tipo or "bike" in tipo: mod,ico = "bike","🚴"
            elif "run"  in tipo:                   mod,ico = "run","🏃"
            else:                                  mod,ico = "other","⚡"

            dist = a.get("distance"); dur = a.get("duration")
            fc   = a.get("averageHR"); fc_max = a.get("maxHR")
            tss  = a.get("trainingStressScore")
            np   = a.get("avgPower"); cad = a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute")
            elev = a.get("elevationGain"); cal_act = a.get("calories")
            aerobic_te = a.get("aerobicTrainingEffect")
            anaerobic_te = a.get("anaerobicTrainingEffect")

            # HR drift proxy: diff entre 1a e 2a metade (não disponível direto — usa desvio como proxy)
            hr_drift = None

            # Pace / speed
            if mod=="run":   perf=pace_run(dur,dist);  pl="Pace"
            elif mod=="bike": perf=spd_bike(dur,dist); pl="Velocidade"
            elif mod=="swim": perf=pace_swim(dur,dist); pl="Pace"
            else:             perf="—"; pl="—"

            # IF (Intensity Factor) se tiver NP e FTP — usamos avgPower como proxy
            # SWOLF para natação
            swolf = a.get("avgStrokes")  # Garmin pode retornar como avgStrokes

            d["ontem"].append({
                "mod":mod,"icone":ico,"nome":a.get("activityName",mod),
                "dist":dist_fmt(dist,mod),"dur":hms(dur),
                "fc":fc,"fc_max":fc_max,"cal":cal_act,"cad":cad,
                "tss":tss,"np":np,"swolf":swolf,
                "aerobic_te":aerobic_te,"anaerobic_te":anaerobic_te,
                "elev":elev,"perf":perf,"pl":pl,
                "_dist_m":dist,"_dur_s":dur,
            })
        print(f"  Treinos ontem: {len(d['ontem'])}")
    except: traceback.print_exc()

    # ─ Atividades de HOJE (treino já feito antes do briefing) ─
    try:
        acts_hoje = api.get_activities_by_date(TODAY_STR, TODAY_STR)
        for a in (acts_hoje or []):
            tipo = (a.get("activityType",{}).get("typeKey") or "").lower()
            if   "swim" in tipo or "pool" in tipo: mod,ico = "swim","🏊"
            elif "cycl" in tipo or "bike" in tipo: mod,ico = "bike","🚴"
            elif "run"  in tipo:                   mod,ico = "run","🏃"
            else:                                  mod,ico = "other","⚡"
            dist = a.get("distance"); dur = a.get("duration")
            fc = a.get("averageHR"); fc_max = a.get("maxHR")
            tss = a.get("trainingStressScore"); np = a.get("avgPower")
            if mod=="run":    perf=pace_run(dur,dist);  pl="Pace"
            elif mod=="bike": perf=spd_bike(dur,dist);  pl="Velocidade"
            elif mod=="swim": perf=pace_swim(dur,dist); pl="Pace"
            else:             perf="—";                 pl="—"
            d["hoje_feito"].append({
                "mod":mod,"icone":ico,"nome":a.get("activityName",mod),
                "dist":dist_fmt(dist,mod),"dur":hms(dur),
                "fc":fc,"fc_max":fc_max,"tss":tss,"np":np,
                "perf":perf,"pl":pl,"_dist_m":dist,"_dur_s":dur,
                "swolf":a.get("avgStrokes"),
                "cad":a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute"),
                "cal":a.get("calories"),
                "aerobic_te":a.get("aerobicTrainingEffect"),
                "anaerobic_te":a.get("anaerobicTrainingEffect"),
                "elev":a.get("elevationGain"),
            })
        if d["hoje_feito"]:
            print(f"  Treino já feito hoje: {len(d['hoje_feito'])} atividade(s)")
    except Exception as e:
        print(f"  Atividades hoje err: {e}")

    # ─ Calendário hoje e amanhã ─
    def _parse_calendar(target_date_str, target_date):
        items = []
        try:
            res = api.get_scheduled_workouts(target_date.year, target_date.month)
            raw = res.get("calendarItems",[]) if isinstance(res,dict) else (res if isinstance(res,list) else [])
            print(f"  Calendário {target_date_str}: {len(raw)} itens no mês")
            for w in raw:
                if not isinstance(w,dict): continue
                wd = w.get("date") or w.get("scheduledDate") or w.get("calendarDate") or ""
                if target_date_str not in str(wd): continue
                if "activity" in str(w.get("itemType","")).lower(): continue
                nome = w.get("title") or w.get("workoutName") or w.get("description") or "Treino"
                tp_raw = w.get("activityType") or w.get("sportType") or ""
                tp = (tp_raw.get("typeKey") or tp_raw.get("sportTypeKey") or str(tp_raw)).lower() if isinstance(tp_raw,dict) else str(tp_raw).lower()

                # Duração: tenta todos os campos possíveis
                dur_w = (w.get("duration") or w.get("estimatedDurationInSecs")
                         or w.get("durationInSeconds") or w.get("workoutDurationInSeconds"))

                # Distância: tenta todos os campos possíveis
                dist_w = (w.get("distance") or w.get("estimatedDistanceInMeters")
                          or w.get("distanceInMeters") or w.get("workoutDistanceInMeters"))

                # Fallback: extrai duração do nome se estiver no formato "1 hr 5 min ..."
                if not dur_w and nome:
                    import re as _re
                    m = _re.match(r'(\d+)\s*hr\s*(\d+)\s*min', nome)
                    if m:
                        dur_w = int(m.group(1))*3600 + int(m.group(2))*60
                        # Limpa o nome removendo a duração do início
                        nome = _re.sub(r'^\d+\s*hr\s*\d+\s*min\s*', '', nome).strip()
                    else:
                        m2 = _re.match(r'(\d+)\s*hr\s*', nome)
                        if m2:
                            dur_w = int(m2.group(1))*3600
                            nome = _re.sub(r'^\d+\s*hr\s*', '', nome).strip()
                        else:
                            m3 = _re.match(r'(\d+)\s*min\s*', nome)
                            if m3:
                                dur_w = int(m3.group(1))*60
                                nome = _re.sub(r'^\d+\s*min\s*', '', nome).strip()

                tp_k = str(w.get('sportTypeKey') or '').lower()
                tp_all = tp_k or tp
                n = nome.lower()
                if   any(x in tp_all for x in ['swim','pool','natac']) or any(x in n for x in ['swim','natac','piscina','pool']): ico='🏊'
                elif any(x in tp_all for x in ['cycl','bike']) or any(x in n for x in ['bike','ride','cicl','ciclismo','bicicl']): ico='🚴'
                elif any(x in tp_all for x in ['run','tread']) or any(x in n for x in ['run','corrida','correr']): ico='🏃'
                else: ico='⚡'
                items.append({"icone":ico,"nome":nome,"tipo":tp,"dur":hms(dur_w),"dist":dist_fmt(dist_w)})
        except Exception as e:
            print(f"  Calendário err {target_date_str}: {e}")
            import traceback; traceback.print_exc()
        return items

    d["hoje"]  = _parse_calendar(TODAY_STR,  TODAY)
    d["amanha"] = _parse_calendar(TOMORROW_STR, TOMORROW)
    print(f"  Hoje: {len(d['hoje'])} treino(s) | Amanhã: {len(d['amanha'])} treino(s)")
    return d

# ─── Prompt técnico ───────────────────────────────────────────────────────────
def gerar_insights(dados):
    s = dados["saude"]

    def fmt_treinos(lista):
        if not lista: return "  Nenhum treino registrado."
        out = ""
        for t in lista:
            out += f"\n  [{t['mod'].upper()}] {t['nome']}: {t['dist']}, {t['dur']}, FC {t['fc']}bpm (max {t['fc_max']}bpm)"
            if t['np']:    out += f", Potência avg {t['np']}W"
            if t['tss']:   out += f", TSS {t['tss']}"
            if t['swolf']: out += f", SWOLF {t['swolf']}"
            if t['cad']:   out += f", Cad {t['cad']}"
            if t['aerobic_te']: out += f", TE aeróbico {t['aerobic_te']}"
            out += f", Pace/Vel: {t['perf']}"
        return out

    def fmt_cal(lista, label):
        if not lista: return f"  Sem treino agendado {label}."
        return "".join(f"\n  {c['icone']} {c['nome']} ({c['dur']}, {c['dist']})" for c in lista)

    hrv_trend = "—"
    if s.get("hrv") and s.get("hrv_7d"):
        diff = float(s["hrv"]) - float(s["hrv_7d"])
        hrv_trend = f"+{diff:.0f}ms vs média 7d" if diff>=0 else f"{diff:.0f}ms vs média 7d"

    prompt = f"""Você é um treinador de triathlon especializado em Half Ironman com abordagem altamente analítica.
Gere um briefing técnico, direto e objetivo. Máximo 2000 caracteres no campo "briefing".

RECUPERAÇÃO ({TODAY_STR}):
- HRV: {s.get('hrv')} ms | Média 7d: {s.get('hrv_7d')} ms | Tendência: {hrv_trend} | Status: {s.get('hrv_status')} | Baseline: {s.get('hrv_baseline_low')}–{s.get('hrv_baseline_high')} ms
- Resting HR: {s.get('rhr')} bpm | Baseline: {s.get('rhr_baseline')} bpm
- Sono: {s.get('sono_h')}h | Score: {s.get('sono_score')} | Deep: {s.get('sono_deep')}h | REM: {s.get('sono_rem')}h
- Body Battery: {s.get('body_battery')}/100 | Stress médio: {s.get('stress_avg')}
- Training Readiness Garmin: {s.get('training_readiness')}

CARGA (Garmin 965 — modelo mensal por zona):
- Carga mensal total: {s.get('load_total_month')} | Target Z1: {s.get('load_target_low')} | Target Z2: {s.get('load_target_high')}
- Zona Aeróbica Leve (Z1): {s.get('load_aerobic_low')} | Zona Aeróbica Alta (Z2): {s.get('load_aerobic_high')} | Anaeróbica (Z3+): {s.get('load_anaerobic')}
- ATL estimado (~semanal): {s.get('atl')} | CTL estimado (~mensal): {s.get('ctl')}
- Status Garmin: {s.get('training_status')} | Tendência de Fitness: {s.get('fitness_trend')}
- VO2max corrida: {s.get('vo2max')} ml/kg/min

TREINO DE ONTEM ({YESTERDAY_STR}):{fmt_treinos(dados['ontem'])}

TREINO HOJE ({TODAY_STR}):{fmt_cal(dados['hoje'], 'para hoje')}
TREINO JÁ EXECUTADO HOJE:{fmt_treinos(dados['hoje_feito']) if dados['hoje_feito'] else "  Nenhum treino registrado ainda hoje."}

TREINO AMANHÃ ({TOMORROW_STR}):{fmt_cal(dados['amanha'], 'para amanhã')}

Responda SOMENTE em JSON válido, sem markdown:
{{
  "frase": "Frase motivacional técnica curta (máx 12 palavras, português)",
  "briefing": "Briefing completo do treinador seguindo esta estrutura:\\n1) READINESS — avalie HRV+tendência, RHR, sono, Body Battery\\n2) TREINO ONTEM — análise por modalidade: eficiência, execução, pontos críticos\\n3) CARGA — distribuição de zonas, risco de fadiga/overreaching\\n4) ANÁLISE DO TREINO DE HOJE (se já executado: analise a execução real vs planejado; se não: oriente como executar) — tipo de sessão, zonas alvo, objetivo fisiológico, como executar dado o estado atual\\n5) AJUSTE CONCRETO — o que manter ou modificar com valores exatos (ex: reduzir Z3 de 20min para 12min)\\n6) ALERTAS — HR drift, sinais de alerta, fadiga acumulada\\n7) NUTRIÇÃO — 1 dica específica pré/durante/pós treino de hoje\\nSeja técnico e objetivo. Máx 2000 caracteres.",
  "status_readiness": "ÓTIMO | BOM | MODERADO | BAIXO | CRÍTICO",
  "status_carga": "SUAVE | IDEAL | ELEVADA | SOBRECARGA",
  "acao_hoje": "MANTER | REDUZIR 20% | REDUZIR 40% | SUBSTITUIR | DESCANSO",
  "acao_hoje": "MANTER | REDUZIR 20% | REDUZIR 40% | SUBSTITUIR | DESCANSO",
  "analise_hoje": "Análise técnica do treino de hoje: tipo de sessão, zonas alvo, duração ideal, principais pontos de execução e atenção. 2-3 frases diretas.",
  "alerta": "Alerta crítico se houver, senão null",
  "treino_forca": [
    {{"exercicio": "Nome do exercício", "series": 3, "repeticoes": "10-12", "carga": "moderada", "foco": "Por que este exercício para triatleta 70.3"}},
    ...
  ]
}}
Regras para treino_forca:
- Escolha 5 a 7 exercícios adequados para triatleta 70.3 baseados no estado atual (readiness {s.get('body_battery')}, carga {s.get('training_status')})
- Se readiness < 40 ou status = Recuperação: exercícios leves, mobilidade, core suave
- Se readiness 40-70: força funcional moderada, glúteos, core, estabilidade
- Se readiness > 70: força explosiva, pliometria, potência
- Sempre inclua: 1 exercício de core, 1 de mobilidade/flexibilidade
- carga deve ser: "leve", "moderada" ou "pesada"
- foco: 1 frase curta explicando o benefício para triathlon
- "series" deve ser número inteiro, "repeticoes" pode ser string como "10-12" ou "30s"
Retorne EXATAMENTE o JSON acima preenchido. Nenhum texto fora do JSON."""

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "messages": [{"role":"user","content":prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
        raw = result["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        print(f"API err {e.code}: {e.read().decode()}")
        return {"frase":"Foco no processo.","briefing":"Erro ao consultar IA.","status_readiness":"—","status_carga":"—","acao_hoje":"—","alerta":None,"treino_forca":[]}
    except Exception as e:
        print(f"Parse err: {e}")
        return {"frase":"Foco no processo.","briefing":str(e),"status_readiness":"—","status_carga":"—","acao_hoje":"—","alerta":None,"treino_forca":[]}

# ─── HTML ─────────────────────────────────────────────────────────────────────
def _sem(v,lo,hi):
    if v is None: return "⚪","#1c1c1c","#555"
    if float(v)>=hi: return "🟢","#00C896","#003a28"
    if float(v)>=lo: return "🟡","#FFB800","#3a2a00"
    return "🔴","#FF4444","#3a0000"

def gerar_html(dados, ins):
    s = dados["saude"]
    dias_pt = {"Monday":"Segunda","Tuesday":"Terça","Wednesday":"Quarta",
               "Thursday":"Quinta","Friday":"Sexta","Saturday":"Sábado","Sunday":"Domingo"}
    dia = dias_pt.get(TODAY.strftime("%A"),TODAY.strftime("%A")) + ", " + TODAY.strftime("%d/%m/%Y")

    def D(st,c): return f'<div style="{st}">{c}</div>'
    def S(st,c): return f'<span style="{st}">{c}</span>'

    def sbox(em,bg,dk,lb):
        return D(f"background:{bg};border-radius:8px;padding:12px 4px;text-align:center",
            S("font-size:18px;display:block",em)+S(f"font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:{dk};display:block;margin-top:4px",lb))

    def mc(ico,lb,val,sub,has):
        bc="#1a6fff" if has else "#242424"; vc="#fff" if has else "#333"
        return D(f"background:#181818;border-radius:8px;padding:12px 10px;border-left:3px solid {bc}",
            S("font-size:12px",ico)+
            D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444;margin:3px 0 2px",lb)+
            D(f"font-size:17px;font-weight:700;color:{vc};line-height:1",val)+
            D("font-size:10px;color:#444;margin-top:2px",sub))

    # Semáforos
    r_em,r_bg,r_dk = _sem(s.get("body_battery"),40,70)
    h_em,h_bg,h_dk = _sem(s.get("hrv"),40,60)
    sn_em,sn_bg,sn_dk = _sem(s.get("sono_score"),60,80)
    acwr=s.get("acwr")
    if acwr is None:          a_em,a_bg,a_dk="⚪","#1c1c1c","#555"
    elif float(acwr)>1.5:     a_em,a_bg,a_dk="🔴","#FF4444","#3a0000"
    elif float(acwr)>=0.8:    a_em,a_bg,a_dk="🟢","#00C896","#003a28"
    else:                     a_em,a_bg,a_dk="🟡","#FFB800","#3a2a00"

    # Status badges
    status_colors = {"ÓTIMO":"#00C896","BOM":"#00C896","MODERADO":"#FFB800","BAIXO":"#FF8C00","CRÍTICO":"#FF4444",
                     "SUAVE":"#00C896","IDEAL":"#00C896","ELEVADA":"#FFB800","SOBRECARGA":"#FF4444"}
    def badge(txt):
        c=status_colors.get(str(txt).upper(),"#555")
        return S(f"font-size:9px;font-weight:700;letter-spacing:.1em;background:{c}22;color:{c};border:1px solid {c}44;padding:3px 8px;border-radius:4px;text-transform:uppercase",str(txt))

    # Ação hoje
    acao_colors={"MANTER":"#00C896","REDUZIR 20%":"#FFB800","REDUZIR 40%":"#FF8C00","SUBSTITUIR":"#FF4444","DESCANSO":"#888"}
    ac=ins.get("acao_hoje","—")
    ac_c=acao_colors.get(ac.upper(),"#888")

    # Treinos de ontem
    def trow(t):
        return (
            "<tr style='border-bottom:1px solid #1e1e1e'>"
            f"<td style='padding:9px 10px;font-size:16px;width:28px'>{t['icone']}</td>"
            f"<td style='padding:9px 10px;font-size:12px;font-weight:600;color:#ddd'>{t['nome']}</td>"
            f"<td style='padding:9px 10px;font-size:11px;color:#888;white-space:nowrap;font-family:monospace'>{t['dist']}</td>"
            f"<td style='padding:9px 10px;font-size:11px;color:#888;white-space:nowrap;font-family:monospace'>{t['dur']}</td>"
            f"<td style='padding:9px 10px;font-size:11px;color:#888;white-space:nowrap;font-family:monospace'>{t['perf']}</td>"
            f"<td style='padding:9px 10px;font-size:11px;color:#666;white-space:nowrap;font-family:monospace'>FC {fmt(t['fc'],0)} | TSS {fmt(t['tss'],0)}</td>"
            "</tr>"
        )

    def cal_table(lista, label):
        if not lista:
            return D("color:#444;font-size:12px;padding:8px 0",f"Nenhum treino agendado {label}.")
        rows="".join(
            "<tr style='border-bottom:1px solid #1e1e1e'>"
            f"<td style='padding:9px 10px;font-size:16px;width:28px'>{c['icone']}</td>"
            f"<td style='padding:9px 10px;font-size:12px;font-weight:600;color:#ddd'>{c['nome']}</td>"
            f"<td style='padding:9px 10px;font-size:11px;color:#888;font-family:monospace'>{c['dur']}</td>"
            f"<td style='padding:9px 10px;font-size:11px;color:#888;font-family:monospace'>{c['dist']}</td>"
            "</tr>"
            for c in lista
        )
        return (
            "<table style='width:100%;border-collapse:collapse;background:#181818;border-radius:8px;overflow:hidden'>"
            "<thead><tr style='border-bottom:1px solid #2a2a2a'>"
            "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444;text-align:left' colspan='2'>Treino</th>"
            "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444;text-align:left'>Duração</th>"
            "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444;text-align:left'>Distância</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )

    # ── Treino de Força ──
    CARGA_C = {"leve":"#00C896","moderada":"#FFB800","pesada":"#FF4444"}
    forca_itens = ins.get("treino_forca") or []
    if forca_itens:
        def forca_row(ex):
            cg = str(ex.get("carga","")).lower()
            cc = CARGA_C.get(cg,"#555")
            return (
                "<tr style='border-bottom:1px solid #1e1e1e'>"
                "<td style='padding:10px 12px;font-size:13px;font-weight:600;color:#fff'>"+str(ex.get("exercicio","—"))+"</td>"
                "<td style='padding:10px 12px;font-size:12px;color:#aaa;text-align:center;font-family:monospace;white-space:nowrap'>"+str(ex.get("series","—"))+"x"+str(ex.get("repeticoes","—"))+"</td>"
                "<td style='padding:10px 12px;text-align:center'><span style='font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:"+cc+";background:"+cc+"22;border:1px solid "+cc+"44;padding:2px 7px;border-radius:4px'>"+str(ex.get("carga","—"))+"</span></td>"
                "<td style='padding:10px 12px;font-size:11px;color:#555;line-height:1.4'>"+str(ex.get("foco",""))+"</td>"
                "</tr>"
            )
        forca_html = (
            D("height:1px;background:#1e1e1e;margin:16px 0","") +
            D("font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:#333;margin-bottom:10px","&#128170; Treino de Forca — Sugestao IA") +
            "<table style='width:100%;border-collapse:collapse;background:#181818;border-radius:10px;overflow:hidden'>"
            "<thead><tr style='border-bottom:1px solid #2a2a2a'>"
            "<th style='padding:8px 12px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:#444;text-align:left'>Exercicio</th>"
            "<th style='padding:8px 12px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:#444;text-align:center'>Series x Reps</th>"
            "<th style='padding:8px 12px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:#444;text-align:center'>Carga</th>"
            "<th style='padding:8px 12px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:#444;text-align:left'>Foco Triathlon</th>"
            "</tr></thead><tbody>"+"".join(forca_row(ex) for ex in forca_itens)+"</tbody></table>"
        )
    else:
        forca_html = ""

    SL = "font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:#333;margin-bottom:10px"
    DIV = D("height:1px;background:#1e1e1e;margin:16px 0","")
    briefing_html = ins.get("briefing","—").replace("\n","<br>")
    alerta_html = D("background:#110000;border-left:3px solid #FF4444;border-radius:8px;padding:12px 15px;font-size:12px;color:#cc5555;line-height:1.5;margin-top:8px","&#9888; "+str(ins["alerta"])) if ins.get("alerta") else ""

    ontem_rows = "".join(trow(t) for t in dados["ontem"]) if dados["ontem"] else f"<tr><td colspan='6' style='padding:10px;color:#444;font-size:12px'>Nenhum treino registrado</td></tr>"

    prev_html=""
    if s.get("previsoes"):
        pitems="".join(D("background:#181818;border-radius:7px;padding:9px 6px;text-align:center",D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#444",k)+D("font-size:14px;font-weight:700;color:#fff;margin-top:3px;font-family:monospace",hms(v))) for k,v in s["previsoes"].items())
        prev_html=D(SL+";margin-top:16px","&#127937; Previsão de Prova")+D("display:grid;grid-template-columns:repeat(4,1fr);gap:6px",pitems)

    body = (
        D(SL,"Status Geral") +
        D("display:grid;grid-template-columns:repeat(4,1fr);gap:6px",
            sbox(r_em,r_bg,r_dk,"Readiness")+sbox(a_em,a_bg,a_dk,"Carga ACWR")+sbox(h_em,h_bg,h_dk,"HRV")+sbox(sn_em,sn_bg,sn_dk,"Sono"))+
        D("display:flex;gap:8px;margin-top:8px;flex-wrap:wrap",
            D("display:flex;align-items:center;gap:6px",S("font-size:10px;color:#555","Readiness:")+badge(ins.get("status_readiness","—")))+
            D("display:flex;align-items:center;gap:6px",S("font-size:10px;color:#555","Carga:")+badge(ins.get("status_carga","—")))+
            D("display:flex;align-items:center;gap:6px",S("font-size:10px;color:#555","Ação hoje:")+S(f"font-size:9px;font-weight:700;letter-spacing:.1em;background:{ac_c}22;color:{ac_c};border:1px solid {ac_c}44;padding:3px 8px;border-radius:4px",ac)))+
        D(SL+";margin-top:20px","Dados Fisiológicos") +
        D("display:grid;grid-template-columns:repeat(3,1fr);gap:6px",
            mc("💤","Sono",fmt(s.get("sono_h"))+"h",f"Score {fmt(s.get('sono_score'),0)} · REM {fmt(s.get('sono_rem'))}h",bool(s.get("sono_h")))+
            mc("❤️","HRV",fmt(s.get("hrv"),0)+" ms",f"7d avg {fmt(s.get('hrv_7d'),0)} · {(s.get('hrv_status') or '—').upper()}",bool(s.get("hrv")))+
            mc("🔋","Body Battery",fmt(s.get("body_battery"),0),f"Stress {fmt(s.get('stress_avg'),0)}",bool(s.get("body_battery")))+
            mc("💓","Resting HR",fmt(s.get("rhr"),0)+" bpm",f"Baseline {fmt(s.get('rhr_baseline'),0)} bpm",bool(s.get("rhr")))+
            mc("📈","ATL / CTL",f"{fmt(s.get('atl'),0)} / {fmt(s.get('ctl'),0)}",f"TSB {fmt(s.get('tsb'),0)} · ACWR {fmt(s.get('acwr'),2)}",bool(s.get("atl")))+
            mc("🫁","VO₂max",fmt(s.get("vo2max"),1),"ml/kg/min",bool(s.get("vo2max"))))+
        DIV+
        D(SL,"Treinos de Ontem") +
        "<table style='width:100%;border-collapse:collapse;background:#181818;border-radius:8px;overflow:hidden'>"
        "<thead><tr style='border-bottom:1px solid #2a2a2a'>"
        "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#444;text-align:left' colspan='2'>Atividade</th>"
        "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#444;text-align:left'>Dist</th>"
        "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#444;text-align:left'>Dur</th>"
        "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#444;text-align:left'>Pace/Vel</th>"
        "<th style='padding:7px 10px;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#444;text-align:left'>FC / TSS</th>"
        f"</tr></thead><tbody>{ontem_rows}</tbody></table>"+
        DIV+
        D(SL,"Análise do Coach — IA") +
        D("background:#07111f;border-left:3px solid #1a6fff;border-radius:8px;padding:14px 16px;font-size:13px;color:#9ab8d8;line-height:1.7",briefing_html)+
        alerta_html+
        DIV+
        D(SL,"Treino de Hoje") + cal_table(dados["hoje"],"para hoje")+
        D(SL,"Treino de Hoje") + cal_table(dados["hoje"],"para hoje")+
        D("background:#071a0f;border-left:3px solid #00C896;border-radius:8px;padding:13px 15px;margin-top:8px",
          D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:#00C896;margin-bottom:5px","&#127919; Analise do Treino de Hoje")+
          D("font-size:13px;color:#70c090;line-height:1.6",str(ins.get("analise_hoje") or "—")))+
        D("background:#07111f;border-left:3px solid #1a6fff;border-radius:8px;padding:13px 15px;margin-top:8px",
          D("font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:#1a6fff;margin-bottom:5px","&#128295; Ajuste Concreto")+
          D("font-size:13px;color:#9ab8d8;line-height:1.6",str(ins.get("sugestao_ajuste") or ins.get("acao_hoje") or "—")))+
        prev_html+forca_html
    )

    return (
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'></head>"
        "<body style='font-family:Arial,Helvetica,sans-serif;background:#050505;color:#e0e0e0;margin:0;padding:0'>"
        "<div style='max-width:580px;margin:20px auto;background:#0f0f0f;border-radius:14px;overflow:hidden;border:1px solid #222'>"
        "<div style='background:#000;border-bottom:3px solid #1a6fff'>"
        "<div style='padding:18px 24px 0;display:flex;justify-content:space-between;align-items:center'>"
        "<span style='font-size:10px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:#fff;background:#1a6fff;padding:4px 10px;border-radius:4px'>&#9202; Triathlon Coach</span>"
        f"<span style='font-size:11px;color:#444'>{dia}</span>"
        "</div>"
        f"<div style='padding:14px 24px 18px;font-size:20px;font-weight:700;line-height:1.3;color:#fff'>{ins.get('frase','—')}</div>"
        "</div>"
        f"<div style='padding:20px 22px 28px'>{body}</div>"
        f"<div style='background:#000;text-align:center;padding:12px;font-size:9px;color:#2a2a2a;letter-spacing:.1em;text-transform:uppercase'>Garmin Connect + Claude AI · {TODAY_STR}</div>"
        "</div></body></html>"
    )

# ─── Envio SendGrid ───────────────────────────────────────────────────────────
def enviar(html):
    payload=json.dumps({"personalizations":[{"to":[{"email":EMAIL_TO}]}],"from":{"email":EMAIL_FROM,"name":"Triathlon Coach"},"subject":f"&#9202; Coach Report — {TODAY.strftime('%d/%m/%Y')}","content":[{"type":"text/html","value":html}]}).encode()
    req=urllib.request.Request("https://api.sendgrid.com/v3/mail/send",data=payload,headers={"Authorization":f"Bearer {SENDGRID_API_KEY}","Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req) as r: print(f"Email enviado — HTTP {r.status}")

# ─── Main ─────────────────────────────────────────────────────────────────────
def salvar_json(dados, ins):
    """Salva report.json para a PWA."""
    report = {
        "date": TODAY_STR,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "saude": dados["saude"],
        "ontem": dados["ontem"],
        "hoje":  dados["hoje"],
        "amanha":dados["amanha"],
        "hoje_feito":dados["hoje_feito"],
        "insights": ins,
    }
    with open("pwa/report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, default=str, indent=2)
    print("  report.json salvo → pwa/report.json")

def main():
    print(f"[{TODAY_STR}] Iniciando briefing...")
    dados  = coletar()
    print("Gerando análise IA...")
    ins    = gerar_insights(dados)
    print("Briefing:", ins.get("frase"))
    salvar_json(dados, ins)
    html   = gerar_html(dados, ins)
    enviar(html)
    print("Concluído ✅")

if __name__ == "__main__":
    main()
