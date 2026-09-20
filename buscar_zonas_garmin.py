#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Busca as zonas reais do atleta no Garmin Connect (FC máxima e zonas de
FC, FTP de bike, limiar de corrida) e salva em pwa/zonas_atleta.json.

Usado pelo sync_plano_garmin.py para montar alvos NUMÉRICOS reais nos
treinos (ex: "220-260W" em vez de só o texto "Z3"), do jeito que outros
apps de treino estruturado fazem.

Roda no Termux (precisa de login Garmin). Best-effort: qualquer zona que
não conseguir buscar fica de fora — o que já foi buscado ainda funciona.
"""

import os, json, time, datetime
from garminconnect import Garmin

GARMIN_EMAIL    = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
CACHE_DIR       = os.environ.get("GARMIN_CACHE_DIR", "/tmp/garmin_cache")
TODAY           = datetime.date.today()

def garmin_login():
    os.makedirs(CACHE_DIR, exist_ok=True)
    for tentativa in range(3):
        try:
            api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            api.login(tokenstore=CACHE_DIR)
            print("  Sessão restaurada.")
            return api
        except Exception as e1:
            if "429" in str(e1) or "Too Many" in str(e1):
                espera = 90 * (tentativa + 1)
                print(f"  ⏳ Rate limit — aguardando {espera}s...")
                time.sleep(espera)
            else:
                print(f"  Cache inválido ({e1}) — login novo.")
                break
    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()
    try: api.garth.dump(CACHE_DIR)
    except Exception: pass
    print("  Login novo, cache salvo.")
    return api

def fmt_pace(s_km):
    """Segundos/km → 'M:SS/km'"""
    if not s_km: return None
    m, s = divmod(int(round(s_km)), 60)
    return f"{m}:{s:02d}/km"

def main():
    print(f"[{TODAY.isoformat()}] Buscando zonas de treino no Garmin...")
    api = garmin_login()
    zonas = {"atualizado_em": datetime.datetime.utcnow().isoformat() + "Z"}

    # ── 1. FC de limiar (corrida + bike) → zonas de FC (% do LTHR) ───────────────
    # get_userprofile_settings() só tem preferências de idioma/unidade — não tem
    # zonas de FC. As zonas de FC vêm daqui: calculadas a partir da FC de limiar
    # (lactate threshold), que é um dado medido de verdade pelo relógio.
    try:
        lim = api.get_lactate_threshold(latest=True)
        print(f"  [debug] lactate_threshold raw: {lim}")
        shr = (lim or {}).get("speed_and_heart_rate", {}) if isinstance(lim, dict) else {}
        lthr_run  = shr.get("heartRate")
        lthr_bike = shr.get("heartRateCycling")

        if lthr_run and 100 < lthr_run < 220:
            zonas["run_lthr_bpm"] = int(lthr_run)
            # Zonas por % da FC de limiar (modelo Friel/TrainingPeaks)
            pct = {"Z1":(0,.81), "Z2":(.81,.89), "Z3":(.90,.93), "Z4":(.94,.99), "Z5":(1.00,1.10)}
            zonas["hr_zonas"] = {z: [round(lthr_run*lo), round(lthr_run*hi)] for z,(lo,hi) in pct.items()}
            print(f"  ✅ FC de limiar (corrida): {int(lthr_run)}bpm · zonas de FC calculadas")
        else:
            print("  ⚠️ FC de limiar (corrida) não encontrada.")

        if lthr_bike and 100 < lthr_bike < 220:
            zonas["bike_lthr_bpm"] = int(lthr_bike)
            print(f"  ✅ FC de limiar (bike): {int(lthr_bike)}bpm")

        # Pace de limiar: o campo 'speed' que o Garmin devolveu não bateu com
        # nenhuma unidade plausível (m/s dava ~49min/km, km/min dava ~2:58/km —
        # nenhum dos dois é crível). Em vez de arriscar um pace errado, corrida
        # usa FC (acima) até isso ser confirmado.
        speed_raw = shr.get("speed")
        if speed_raw:
            print(f"  ⚠️ Pace de limiar: valor bruto '{speed_raw}' sem unidade confiável — não usado (corrida usa FC).")
    except Exception as e:
        print(f"  ⚠️ FC/pace de limiar: {e}")

    # ── 2. FTP de bike → zonas de potência (modelo padrão Coggan, % do FTP) ──────
    try:
        ftp_res = api.get_cycling_ftp()
        print(f"  [debug] cycling_ftp raw: {ftp_res}")
        ftp = None
        if isinstance(ftp_res, dict):
            ftp = ftp_res.get("functionalThresholdPower") or ftp_res.get("ftpValue") or ftp_res.get("ftp")
        elif isinstance(ftp_res, list) and ftp_res:
            ftp = ftp_res[0].get("functionalThresholdPower") or ftp_res[0].get("ftpValue")
        if ftp and float(ftp) > 30:
            ftp = float(ftp)
            zonas["bike_ftp_w"] = round(ftp)
            # Zonas Coggan clássicas (% do FTP)
            pct = {"Z1":(0,.55), "Z2":(.56,.75), "Z3":(.76,.90), "Z4":(.91,1.05), "Z5":(1.06,1.20), "Z6":(1.21,3.0)}
            zonas["bike_zonas_w"] = {z: [round(ftp*lo), round(ftp*hi)] for z,(lo,hi) in pct.items()}
            print(f"  ✅ FTP: {round(ftp)}W · zonas de potência calculadas")
        else:
            print("  ⚠️ FTP não encontrado (sem histórico de bike com potência).")
    except Exception as e:
        print(f"  ⚠️ FTP: {e}")

    # ── 3. Previsão de 10K (Garmin) → zonas de pace ──────────────────────────────
    # A previsão de corrida do Garmin (mesma que já aparece no app) é um dado
    # confiável e específico do atleta — melhor base para pace do que o campo
    # ambíguo de "speed" do limiar. Usado só para MOSTRAR distância no app;
    # o alvo real enviado ao relógio continua sendo por FC (já comprovado OK).
    try:
        pred = api.get_race_predictions()
        print(f"  [debug] race_predictions raw: {pred}")
        t10k = pred.get("time10K") if isinstance(pred, dict) else None
        if t10k and float(t10k) > 0:
            pace_10k = float(t10k) / 10.0  # s/km
            zonas["run_pace_10k_s_km"] = round(pace_10k)
            # Zonas de pace como % do pace de 10K (pace MAIOR = mais devagar)
            pct = {"Z1":(1.30,1.45), "Z2":(1.15,1.30), "Z3":(1.05,1.15), "Z4":(0.98,1.05), "Z5":(0.90,0.98)}
            zonas["run_zonas_pace_s_km"] = {z: sorted([round(pace_10k*lo), round(pace_10k*hi)]) for z,(lo,hi) in pct.items()}
            print(f"  ✅ Pace de 10K: {fmt_pace(pace_10k)} · zonas de pace calculadas (só para exibição no app)")
        else:
            print("  ⚠️ Previsão de 10K não encontrada.")
    except Exception as e:
        print(f"  ⚠️ Previsão de corrida: {e}")

    os.makedirs("pwa", exist_ok=True)
    with open("pwa/zonas_atleta.json", "w", encoding="utf-8") as f:
        json.dump(zonas, f, ensure_ascii=False, default=str, indent=2)
    print(f"  💾 pwa/zonas_atleta.json salvo — chaves: {list(zonas.keys())}")
    print("Concluído ✅")

if __name__ == "__main__":
    main()
