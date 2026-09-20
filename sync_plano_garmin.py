#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Envia as sessões de natação/bike/corrida do plano de 4 semanas (pwa/plano.json)
para o Garmin Connect como treinos agendados no calendário/relógio.

Nunca toca em atividades fixas (futebol, natação em grupo etc. marcadas como
"fixo") nem em nada que não tenha sido criado pelo próprio app.

Uso:
  python sync_plano_garmin.py            → sincroniza do dia de hoje em diante (todo o plano restante)
  SYNC_DATE=2026-09-22 python sync_plano_garmin.py   → sincroniza só essa data
"""

import os, json, datetime, time

from garminconnect import Garmin

GARMIN_EMAIL    = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
CACHE_DIR       = os.environ.get("GARMIN_CACHE_DIR", "/tmp/garmin_cache")
SYNC_DATE       = os.environ.get("SYNC_DATE", "")  # se vazio, sincroniza tudo a partir de hoje
TODAY           = datetime.date.today()

SPORT_TYPE = {
    "run":  {"sportTypeId": 1, "sportTypeKey": "running"},
    "bike": {"sportTypeId": 2, "sportTypeKey": "cycling"},
    "swim": {"sportTypeId": 3, "sportTypeKey": "swimming"},
}
SPORT_LABEL = {"run": "Corrida", "bike": "Bike", "swim": "Natação"}

IDS_PATH = "pwa/plano_garmin_ids.json"


def _is_429(e):
    return "429" in str(e) or "Too Many" in str(e) or "TooManyRequests" in type(e).__name__


def garmin_login():
    os.makedirs(CACHE_DIR, exist_ok=True)
    for tentativa in range(3):
        try:
            api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
            api.login(tokenstore=CACHE_DIR)
            print("  Sessão restaurada.")
            return api
        except Exception as e1:
            if _is_429(e1):
                espera = 60 * (tentativa + 1)
                print(f"  ⏳ Rate limit — aguardando {espera}s...")
                time.sleep(espera)
            else:
                print(f"  Cache inválido ({e1}) — login novo.")
                break
    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()
    try: api.garth.dump(CACHE_DIR); print("  Login novo, cache salvo.")
    except Exception as e: print(f"  Login ok (sem cache: {e})")
    return api


def montar_payload(date_str, sessao):
    esporte = sessao.get("esporte")
    sport   = SPORT_TYPE.get(esporte)
    if not sport:
        return None
    dur_min = int(sessao.get("duracao_min") or 30)
    nota = f"{sessao.get('zona','')} — {sessao.get('descricao','')}".strip(" —")[:250]
    nome = f"{SPORT_LABEL.get(esporte,esporte)} · {sessao.get('tipo','Treino')}"[:80]

    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
        "endConditionValue": dur_min * 60,
        "description": nota,
    }
    return {
        "workoutName": nome,
        "description": f"TP Performance Coach · plano · {date_str}",
        "sportType": sport,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport,
            "workoutSteps": [step],
        }],
    }


def sync_dia(api, date_str, sessoes, ids_map):
    criados = 0
    for sessao in sessoes:
        esporte = sessao.get("esporte")
        if sessao.get("fixo") or esporte not in SPORT_TYPE:
            continue  # nunca mexe em atividade fixa nem em esportes fora do triatlo

        key = f"{date_str}_{esporte}"
        old_id = ids_map.get(key)
        if old_id:
            try:
                api.garth.delete("connectapi", f"/workout-service/workout/{old_id}", api=True)
            except Exception:
                pass  # já pode ter sido apagado manualmente — segue o jogo

        payload = montar_payload(date_str, sessao)
        if not payload:
            continue
        try:
            res = api.upload_workout(payload)
            wid = res.get("workoutId")
            if wid:
                api.schedule_workout(wid, date_str)
                ids_map[key] = wid
                criados += 1
                print(f"  ✅ {date_str} {SPORT_LABEL.get(esporte,esporte)}: workout {wid} agendado")
        except Exception as e:
            print(f"  ⚠️ Falha em {date_str} {esporte}: {e}")
    return criados


def main():
    print(f"[{TODAY.isoformat()}] Sincronizando plano com o Garmin...")

    with open("pwa/plano.json", encoding="utf-8") as f:
        plano = json.load(f)

    if os.path.exists(IDS_PATH):
        with open(IDS_PATH, encoding="utf-8") as f:
            ids_map = json.load(f)
    else:
        ids_map = {}

    dias_alvo = {}
    for sem in plano.get("semanas", []):
        for dt, info in sem.get("dias", {}).items():
            if SYNC_DATE:
                if dt == SYNC_DATE:
                    dias_alvo[dt] = info.get("sessoes", [])
            else:
                if dt >= TODAY.isoformat():
                    dias_alvo[dt] = info.get("sessoes", [])

    if not dias_alvo:
        print("  Nada para sincronizar (data fora do plano ou plano vazio).")
        return

    print(f"  {len(dias_alvo)} dia(s) para processar...")
    api = garmin_login()

    total = 0
    for dt in sorted(dias_alvo):
        total += sync_dia(api, dt, dias_alvo[dt], ids_map)

    with open(IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(ids_map, f, ensure_ascii=False, indent=2)

    print(f"  {total} treino(s) enviado(s) ao Garmin.")
    print("Concluído ✅")


if __name__ == "__main__":
    main()
