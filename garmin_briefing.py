#!/usr/bin/env python3
"""Garmin Triathlon Briefing v3 — Coach técnico para Half Ironman."""

import os, json, datetime, traceback, urllib.request, urllib.error, pickle, pathlib
from garminconnect import Garmin

# ─── Config ──────────────────────────────────────────────────────────────────
GARMIN_EMAIL      = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD   = os.environ["GARMIN_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

TODAY     = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
TOMORROW  = TODAY + datetime.timedelta(days=1)
TODAY_STR     = TODAY.isoformat()
YESTERDAY_STR = YESTERDAY.isoformat()
TOMORROW_STR  = TOMORROW.isoformat()
CACHE_DIR     = "/tmp/garmin_cache"

# Notificação push via ntfy.sh (grátis, sem servidor)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # ex: "tp-coach-abc123"

# Data da próxima prova — configure RACE_DATE="YYYY-MM-DD" em GitHub Secrets
_race_env = os.environ.get("RACE_DATE","")
try:    RACE_DATE = datetime.date.fromisoformat(_race_env) if _race_env else None
except: RACE_DATE = None

# Localização para clima (padrão: Erkrath)
WEATHER_LAT = os.environ.get("WEATHER_LAT", "51.2227")
WEATHER_LON = os.environ.get("WEATHER_LON", "6.9116")

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
    d = {"saude":{}, "ontem":[], "hoje":[], "amanha":[], "hoje_feito":[], "semana":{}, "semana_passada":{}, "clima":{}, "hrv_7dias":[], "vo2max_hist":[], "prs":[], "sono_performance":[]}
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

    # ─── 1. HRV últimos 7 dias ─────────────────────────────────────────────────
    try:
        hrv_series = []
        for i in range(6, -1, -1):
            day = (TODAY - datetime.timedelta(days=i)).isoformat()
            try:
                r7 = api.get_hrv_data(day)
                v = (r7.get("hrvSummary",{}).get("lastNight5MinHigh")
                     or r7.get("hrvSummary",{}).get("lastNightAvg"))
                hrv_series.append({"date": day, "value": round(float(v),0) if v else None})
            except: hrv_series.append({"date": day, "value": None})
        d["hrv_7dias"] = hrv_series
        print(f"  HRV 7d: {[x['value'] for x in hrv_series]}")
    except Exception as e: print(f"  HRV 7d err: {e}")

    # ─── 2. Volume semanal por modalidade ───────────────────────────────────────
    try:
        semana_inicio = (TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat()
        acts_semana = api.get_activities_by_date(semana_inicio, TODAY_STR) or []
        sem = {"swim_km":0,"swim_min":0,"bike_km":0,"bike_min":0,"run_km":0,"run_min":0,"total_tss":0,"n_treinos":0}
        for a in acts_semana:
            tipo = (a.get("activityType",{}).get("typeKey") or "").lower()
            dist = float(a.get("distance") or 0) / 1000
            dur  = float(a.get("duration") or 0) / 60
            tss  = float(a.get("trainingStressScore") or 0)
            sem["total_tss"] += tss
            sem["n_treinos"]  += 1
            if "swim" in tipo or "pool" in tipo: sem["swim_km"]+=dist; sem["swim_min"]+=dur
            elif "cycl" in tipo or "bike" in tipo: sem["bike_km"]+=dist; sem["bike_min"]+=dur
            elif "run" in tipo: sem["run_km"]+=dist; sem["run_min"]+=dur
        sem = {k: round(v,1) for k,v in sem.items()}
        d["semana"] = sem
        print(f"  Semana: swim={sem['swim_km']}km bike={sem['bike_km']}km run={sem['run_km']}km")
    except Exception as e: print(f"  Semana err: {e}")

    # ─── 2b. Volume semana passada (para comparativo) ──────────────────────────
    try:
        seg_passada = TODAY - datetime.timedelta(days=TODAY.weekday()+7)
        dom_passada = seg_passada + datetime.timedelta(days=6)
        acts_lw = api.get_activities_by_date(seg_passada.isoformat(), dom_passada.isoformat()) or []
        lw = {"swim_km":0,"swim_min":0,"bike_km":0,"bike_min":0,"run_km":0,"run_min":0}
        for a in acts_lw:
            tipo = (a.get("activityType",{}).get("typeKey") or "").lower()
            dist = float(a.get("distance") or 0)/1000
            dur  = float(a.get("duration") or 0)/60
            if "swim" in tipo or "pool" in tipo: lw["swim_km"]+=dist; lw["swim_min"]+=dur
            elif "cycl" in tipo or "bike" in tipo: lw["bike_km"]+=dist; lw["bike_min"]+=dur
            elif "run" in tipo: lw["run_km"]+=dist; lw["run_min"]+=dur
        d["semana_passada"] = {k:round(v,1) for k,v in lw.items()}
        print(f"  Semana passada: swim={lw['swim_km']:.1f}km bike={lw['bike_km']:.1f}km run={lw['run_km']:.1f}km")
    except Exception as e: print(f"  Semana passada err: {e}"); d["semana_passada"]={}

    # ─── 2c. VO2max últimos 30 dias ──────────────────────────────────────────────
    try:
        vo2_hist = []
        for i in range(29, -1, -1):
            day = (TODAY - datetime.timedelta(days=i)).isoformat()
            try:
                ts_d = api.get_training_status(day)
                vn = ts_d.get("mostRecentVO2Max",{}).get("generic",{})
                v  = vn.get("vo2MaxPreciseValue") or vn.get("vo2MaxValue")
                if v: vo2_hist.append({"date":day,"value":round(float(v),1)})
            except: pass
        d["vo2max_hist"] = vo2_hist[-15:]  # últimos 15 com valor
        print(f"  VO2max hist: {len(d['vo2max_hist'])} pontos")
    except Exception as e: print(f"  VO2max hist err: {e}"); d["vo2max_hist"]=[]

    # ─── 2d. PRs automáticos (compara com últimos 90 dias) ───────────────────────
    try:
        acts_90 = api.get_activities_by_date(
            (TODAY - datetime.timedelta(days=90)).isoformat(), YESTERDAY_STR) or []
        best = {"run_pace_s":999999,"bike_kmh":0,"swim_pace_s":999999}
        for a in acts_90:
            tipo = (a.get("activityType",{}).get("typeKey") or "").lower()
            dist = float(a.get("distance") or 0)
            dur  = float(a.get("duration") or 1)
            if "run" in tipo and dist>1000:
                p = dur/dist*1000
                if p < best["run_pace_s"]: best["run_pace_s"]=p
            elif ("cycl" in tipo or "bike" in tipo) and dist>5000:
                spd = (dist/1000)/(dur/3600)
                if spd > best["bike_kmh"]: best["bike_kmh"]=round(spd,1)
            elif ("swim" in tipo or "pool" in tipo) and dist>100:
                p = dur/dist*100
                if p < best["swim_pace_s"]: best["swim_pace_s"]=p

        prs = []
        for t in (d.get("ontem",[]) + d.get("hoje_feito",[])):
            if t["mod"]=="run" and t.get("_dist_m",0)>1000 and t.get("_dur_s"):
                p = t["_dur_s"]/t["_dist_m"]*1000
                if p < best["run_pace_s"]*0.98:
                    prs.append({"tipo":"🏃 Corrida","desc":f"Novo pace: {pace_run(t['_dur_s'],t['_dist_m'])}"})
            elif t["mod"]=="bike" and t.get("_dist_m",0)>5000 and t.get("_dur_s"):
                spd = (t["_dist_m"]/1000)/(t["_dur_s"]/3600)
                if spd > best["bike_kmh"]*1.02:
                    prs.append({"tipo":"🚴 Ciclismo","desc":f"Nova velocidade: {spd:.1f}km/h"})
        d["prs"] = prs
        if prs: print(f"  PRs detectados: {len(prs)}")
    except Exception as e: print(f"  PRs err: {e}"); d["prs"]=[]

    # ─── 2e. Correlação sono × desempenho (últimos 14 dias) ──────────────────────
    try:
        corr_data = []
        for i in range(13, -1, -1):
            day = (TODAY - datetime.timedelta(days=i)).isoformat()
            try:
                sl = api.get_sleep_data(day)
                sh = round((sl.get("dailySleepDTO",{}).get("sleepTimeSeconds") or 0)/3600, 1)
                acts_day = api.get_activities_by_date(day, day) or []
                te = max((float(a.get("aerobicTrainingEffect") or 0) for a in acts_day), default=None)
                if sh > 0 and te:
                    corr_data.append({"date":day,"sono":sh,"te":round(te,1)})
            except: pass
        d["sono_performance"] = corr_data
        print(f"  Sono×performance: {len(corr_data)} dias com dados")
    except Exception as e: print(f"  Sono×perf err: {e}"); d["sono_performance"]=[]

    # ─── 3. Risco de lesão (spike ATL) ──────────────────────────────────────────
    try:
        atl = float(s.get("atl") or 0)
        ctl = float(s.get("ctl") or 1)
        acwr = atl / ctl if ctl > 0 else 0
        if   acwr > 1.5: s["risco_lesao"] = "ALTO";   s["risco_cor"] = "#FF4444"
        elif acwr > 1.3: s["risco_lesao"] = "ELEVADO"; s["risco_cor"] = "#FF8C00"
        elif acwr >= 0.8:s["risco_lesao"] = "BAIXO";   s["risco_cor"] = "#00C896"
        else:            s["risco_lesao"] = "MODERADO"; s["risco_cor"] = "#FFB800"
        print(f"  Risco lesão: {s['risco_lesao']} (ACWR={acwr:.2f})")
    except: s["risco_lesao"] = None; s["risco_cor"] = "#555"

    # ─── 4. Countdown da prova + fase de treino ──────────────────────────────────
    try:
        if RACE_DATE:
            dias = (RACE_DATE - TODAY).days
            s["prova_dias"]  = dias
            s["prova_data"]  = RACE_DATE.strftime("%d/%m/%Y")
            if   dias <= 7:  s["prova_fase"] = "TAPER FINAL"
            elif dias <= 21: s["prova_fase"] = "TAPER"
            elif dias <= 42: s["prova_fase"] = "PEAK"
            elif dias <= 84: s["prova_fase"] = "BUILD"
            else:            s["prova_fase"] = "BASE"
            print(f"  Prova: {dias} dias ({s['prova_fase']})")
        else:
            s["prova_dias"] = s["prova_data"] = s["prova_fase"] = None
    except: s["prova_dias"] = s["prova_data"] = s["prova_fase"] = None

    # ─── 5. Clima (Open-Meteo — gratuito, sem API key) ───────────────────────────
    try:
        url_clima = (f"https://api.open-meteo.com/v1/forecast"
                     f"?latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
                     f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     f"windspeed_10m_max,weathercode"
                     f"&current_weather=true"
                     f"&timezone=Europe%2FBerlin&forecast_days=1")
        with urllib.request.urlopen(url_clima, timeout=10) as r:
            cj = json.loads(r.read())
        daily = cj.get("daily",{}); cur = cj.get("current_weather",{})
        wcode = daily.get("weathercode",[0])[0]
        # WMO weather codes → emoji
        def wmo_emoji(c):
            if c==0: return "☀️"
            if c<=3: return "🌤️"
            if c<=48: return "🌫️"
            if c<=67: return "🌧️"
            if c<=77: return "🌨️"
            if c<=82: return "⛈️"
            return "🌩️"
        d["clima"] = {
            "temp_max": daily.get("temperature_2m_max",[None])[0],
            "temp_min": daily.get("temperature_2m_min",[None])[0],
            "chuva_pct": daily.get("precipitation_probability_max",[0])[0],
            "vento_kmh": daily.get("windspeed_10m_max",[0])[0],
            "temp_atual": cur.get("temperature"),
            "emoji": wmo_emoji(wcode),
        }
        print(f"  Clima: {d['clima']['emoji']} {d['clima']['temp_max']}°C chuva={d['clima']['chuva_pct']}%")
    except Exception as e: print(f"  Clima err: {e}")

    # ─── 6 & 7. Nutrição/Hidratação + Sono ideal ─────────────────────────────────
    try:
        hoje_treinos = d.get("hoje",[])
        dur_total_min = 0
        for t in hoje_treinos:
            dur_s = t.get("_dur_s") or 0
            if not dur_s:
                # parse "2h30m" → minutos
                import re
                m = re.match(r'(\d+)h(\d+)m', t.get("dur",""))
                if m: dur_s = int(m.group(1))*3600 + int(m.group(2))*60
                else:
                    m2 = re.match(r'(\d+)m', t.get("dur",""))
                    if m2: dur_s = int(m2.group(1))*60
            dur_total_min += dur_s / 60

        # Zona dominante do treino de hoje
        zona_hoje = "Z2" if s.get("acao_hoje","MANTER") == "MANTER" else "Z1"
        fator_cho = 60 if zona_hoje == "Z1" else 80  # g CHO/h

        s["nutricao"] = {
            "cho_g_h":    fator_cho,
            "cho_total":  round(fator_cho * dur_total_min / 60, 0) if dur_total_min > 0 else None,
            "agua_ml_h":  600 if d["clima"].get("temp_max",20) < 20 else 750,
            "agua_total": round(600 * dur_total_min / 60, 0) if dur_total_min > 0 else None,
            "pre_kcal":   350,
            "pos_prot_g": 30,
            "dur_min":    round(dur_total_min, 0),
        }

        # Sono ideal: 8h mínimo, ajustado pela carga
        carga_hoje = s.get("load_total_month",0) or 0
        sono_alvo = 8.5 if carga_hoje > 2500 else 8.0
        # Horário para dormir = próximo treino duro - sono_alvo
        # Simplificado: sugere horário fixo com base no treino de amanhã
        s["sono_alvo_h"]   = sono_alvo
        # Acordar às 06:30 → deitar = 06:30 - sono_alvo
        wake_h = 6.5
        bed_h = (wake_h - sono_alvo) % 24
        bed_hh = int(bed_h)
        bed_mm = "30" if (bed_h % 1) >= 0.5 else "00"
        s["sono_deita"] = f"{bed_hh:02d}:{bed_mm}"
        s["sono_deficit_h"]= round(sono_alvo - (s.get("sono_h") or 0), 1) if s.get("sono_h") else None

        print(f"  Nutrição: CHO {s['nutricao']['cho_g_h']}g/h | Água {s['nutricao']['agua_ml_h']}ml/h")
        print(f"  Sono alvo: {sono_alvo}h | Deitar: {s['sono_deita']}")
    except Exception as e: print(f"  Nutrição/Sono err: {e}")
        
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
- ATL estimado: {s.get('atl')} | CTL estimado: {s.get('ctl')} | Risco de Lesão: {s.get('risco_lesao')}
- Status Garmin: {s.get('training_status')} | Tendência: {s.get('fitness_trend')} | VO2max: {s.get('vo2max')} ml/kg/min
- VOLUME SEMANAL: Natação {dados['semana'].get('swim_km',0)}km | Bike {dados['semana'].get('bike_km',0)}km | Corrida {dados['semana'].get('run_km',0)}km

PROVA & CONTEXTO:
- Próxima prova: {s.get('prova_data','não configurada')} | Dias restantes: {s.get('prova_dias','—')} | Fase: {s.get('prova_fase','—')}
- Clima hoje: {dados['clima'].get('emoji','')} {dados['clima'].get('temp_max','—')}°C max / {dados['clima'].get('temp_min','—')}°C min | Chuva: {dados['clima'].get('chuva_pct','—')}% | Vento: {dados['clima'].get('vento_kmh','—')}km/h

NUTRIÇÃO ESTIMADA PARA HOJE:
- Duração total treinos: {s.get('nutricao',{}).get('dur_min','—')} min
- CHO: {s.get('nutricao',{}).get('cho_g_h','—')}g/h ({s.get('nutricao',{}).get('cho_total','—')}g total)
- Hidratação: {s.get('nutricao',{}).get('agua_ml_h','—')}ml/h | Pré-treino: {s.get('nutricao',{}).get('pre_kcal','—')}kcal | Pós: {s.get('nutricao',{}).get('pos_prot_g','—')}g proteína
- Sono alvo: {s.get('sono_alvo_h','—')}h | Déficit atual: {s.get('sono_deficit_h','—')}h | Deitar às: {s.get('sono_deita','—')}

TREINO DE ONTEM ({YESTERDAY_STR}):{fmt_treinos(dados['ontem'])}

TREINO HOJE ({TODAY_STR}):{fmt_cal(dados['hoje'], 'para hoje')}
TREINO JÁ EXECUTADO HOJE:{fmt_treinos(dados['hoje_feito']) if dados['hoje_feito'] else "  Nenhum treino registrado ainda hoje."}

TREINO AMANHÃ ({TOMORROW_STR}):{fmt_cal(dados['amanha'], 'para amanhã')}

Responda SOMENTE em JSON válido, sem markdown:
{{
  "frase": "Frase motivacional técnica curta (máx 12 palavras, português)",
  "sec_readiness": "2-3 frases: HRV {s.get('hrv')}ms vs tendência 7d, RHR, sono (qualidade/déficit), Body Battery. Direto e quantitativo.",
  "sec_treino_ontem": "2-3 frases: análise das modalidades executadas ontem — eficiência, execução, pontos críticos. Se não houve treino: null.",
  "sec_carga": "2-3 frases: distribuição Z1/Z2/Z3 vs targets, risco de fadiga, fase {s.get('prova_fase','—')}. Seja crítico.",
  "sec_hoje": "2-3 frases: análise do treino de hoje — se já executado analise real vs planejado; se não, como executar e zonas alvo exatas.",
  "sec_ajuste": "1-2 frases com valores EXATOS: ex. Manter 2h30 mas limitar Z3 a 10min. FC teto 155bpm. Potência alvo 200-220W.",
  "sec_alertas": "Alertas técnicos específicos: HR drift, fadiga, distribuição zonas, sobrecarga. Se nada crítico: null.",
  "sec_nutricao": "1-2 frases específicas: pré/durante/pós treino de hoje com valores reais.",
  "status_readiness": "ÓTIMO | BOM | MODERADO | BAIXO | CRÍTICO",
  "status_carga": "SUAVE | IDEAL | ELEVADA | SOBRECARGA",
  "acao_hoje": "MANTER | REDUZIR 20% | REDUZIR 40% | SUBSTITUIR | DESCANSO",
  "analise_hoje": "Análise técnica do treino de hoje em 1-2 frases diretas.",
  "alerta": "Alerta crítico em 1 frase se houver, senão null",
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
        "max_tokens": 5000,
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


def salvar_json(dados, ins):
    report = {
        "date": TODAY_STR,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "saude":        dados["saude"],
        "ontem":        dados["ontem"],
        "hoje":         dados["hoje"],
        "amanha":       dados["amanha"],
        "hoje_feito":   dados["hoje_feito"],
        "semana":       dados.get("semana",{}),
        "semana_passada":dados.get("semana_passada",{}),
        "clima":        dados.get("clima",{}),
        "hrv_7dias":    dados.get("hrv_7dias",[]),
        "vo2max_hist":  dados.get("vo2max_hist",[]),
        "prs":          dados.get("prs",[]),
        "sono_performance": dados.get("sono_performance",[]),
        "insights":     ins,
    }
    import os
    os.makedirs("pwa", exist_ok=True)
    with open("pwa/report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, default=str, indent=2)
    print("  report.json salvo → pwa/report.json")

def notificar(ins):
    if not NTFY_TOPIC: return
    try:
        hrv = ins.get('sec_readiness','')[:60] if ins.get('sec_readiness') else ''
        msg = f"Briefing pronto! {ins.get('frase','')} | {hrv}"
        req = urllib.request.Request(
            f'https://ntfy.sh/{NTFY_TOPIC}',
            data=msg.encode(),
            headers={'Title':'TP Performance Coach','Priority':'default','Tags':'chart_with_upwards_trend'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=10)
        print('  Notificação enviada via ntfy.sh')
    except Exception as e: print(f'  ntfy err: {e}')

def main():
    print(f'[{TODAY_STR}] Iniciando briefing...')
    dados  = coletar()
    print('Gerando análise IA...')
    ins    = gerar_insights(dados)
    print('Briefing:', ins.get('frase'))
    salvar_json(dados, ins)
    notificar(ins)
    print('Concluído ✅')

if __name__ == '__main__':
    main()
