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

    # ── 1. Zonas de FC (FC máxima + limites de cada zona) ────────────────────────
    try:
        prof = api.get_userprofile_settings()
        print(f"  [debug] userprofile_settings keys: {list(prof.keys()) if isinstance(prof, dict) else type(prof)}")
        # Formatos possíveis variam por conta — tenta os caminhos mais comuns
        hr_zones_raw = None
        if isinstance(prof, dict):
            hr_zones_raw = (prof.get("userData", {}).get("heartRateZones")
                             or prof.get("heartRateZones")
                             or prof.get("userSleep", {}).get("heartRateZones"))
        max_hr = None
        if isinstance(prof, dict):
            max_hr = (prof.get("userData", {}).get("vo2MaxRunning")  # às vezes não é o certo, ignorar se não for número de bpm plausível
                       or prof.get("userData", {}).get("maxHr")
                       or prof.get("maxHr"))

        if hr_zones_raw and isinstance(hr_zones_raw, list):
            hr_z = {}
            for i, z in enumerate(sorted(hr_zones_raw, key=lambda x: x.get("zoneNumber", i)), start=1):
                lo = z.get("zoneLowBoundary") or z.get("lowBoundary")
                hi = z.get("zoneHighBoundary") or z.get("highBoundary")
                if lo is not None and hi is not None:
                    hr_z[f"Z{z.get('zoneNumber', i)}"] = [int(lo), int(hi)]
            if hr_z:
                zonas["hr_zonas"] = hr_z
                print(f"  ✅ Zonas de FC: {hr_z}")
        if max_hr and isinstance(max_hr, (int, float)) and 100 < max_hr < 230:
            zonas["hr_max"] = int(max_hr)
            print(f"  ✅ FC máxima: {int(max_hr)}")

        if "hr_zonas" not in zonas:
            print("  ⚠️ Zonas de FC não encontradas no formato esperado — pulando (veja debug acima).")
    except Exception as e:
        print(f"  ⚠️ Zonas de FC: {e}")

    # ── 2. FTP de bike → zonas de potência (modelo padrão Coggan, % do FTP) ──────
    try:
        ftp_res = api.get_cycling_ftp()
        print(f"  [debug] cycling_ftp raw: {ftp_res}")
        ftp = None
        if isinstance(ftp_res, dict):
            ftp = ftp_res.get("ftpValue") or ftp_res.get("ftp") or ftp_res.get("value")
        elif isinstance(ftp_res, list) and ftp_res:
            ftp = ftp_res[0].get("ftpValue") or ftp_res[0].get("ftp")
        if ftp and float(ftp) > 30:
            ftp = float(ftp)
            zonas["bike_ftp_w"] = round(ftp)
            # Zonas Coggan clássicas (% do FTP)
            pct = {"Z1":(0,.55), "Z2":(.56,.75), "Z3":(.76,.90), "Z4":(.91,1.05), "Z5":(1.06,1.20), "Z6":(1.21,3.0)}
            zonas["bike_zonas_w"] = {z: [round(ftp*lo), round(ftp*hi)] for z,(lo,hi) in pct.items()}
            print(f"  ✅ FTP: {round(ftp)}W · zonas calculadas")
        else:
            print("  ⚠️ FTP não encontrado (sem histórico de bike com potência).")
    except Exception as e:
        print(f"  ⚠️ FTP: {e}")

    # ── 3. Limiar de corrida → zonas de pace (% do pace de limiar) ───────────────
    try:
        lim = api.get_lactate_threshold(latest=True)
        print(f"  [debug] lactate_threshold raw: {lim}")
        pace_limiar = None
        if isinstance(lim, dict):
            # valor pode vir como pace (min/km) ou velocidade (m/s) dependendo da conta
            speed = lim.get("speed") or lim.get("thresholdSpeed")
            pace_direct = lim.get("pace") or lim.get("thresholdPace")
            if speed and float(speed) > 0:
                pace_limiar = 1000.0 / float(speed)  # m/s → s/km
            elif pace_direct:
                pace_limiar = float(pace_direct)
        if pace_limiar and 120 < pace_limiar < 900:  # entre 2:00 e 15:00/km — filtro de sanidade
            zonas["run_pace_limiar_s_km"] = round(pace_limiar)
            # Zonas de pace: % do pace de limiar (pace MAIOR = mais devagar = zona menor)
            pct = {"Z1":(1.30,1.50), "Z2":(1.15,1.30), "Z3":(1.05,1.15), "Z4":(0.97,1.05), "Z5":(0.88,0.97)}
            zonas["run_zonas_pace_s_km"] = {z: sorted([round(pace_limiar*lo), round(pace_limiar*hi)]) for z,(lo,hi) in pct.items()}
            print(f"  ✅ Pace de limiar: {fmt_pace(pace_limiar)} · zonas calculadas")
        else:
            print("  ⚠️ Limiar de corrida não encontrado.")
    except Exception as e:
        print(f"  ⚠️ Limiar de corrida: {e}")

    os.makedirs("pwa", exist_ok=True)
    with open("pwa/zonas_atleta.json", "w", encoding="utf-8") as f:
        json.dump(zonas, f, ensure_ascii=False, default=str, indent=2)
    print(f"  💾 pwa/zonas_atleta.json salvo — chaves: {list(zonas.keys())}")
    print("Concluído ✅")

if __name__ == "__main__":
    main()
