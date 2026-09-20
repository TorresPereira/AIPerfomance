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

import os, json, re, datetime, time

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

IDS_PATH   = "pwa/plano_garmin_ids.json"
ZONAS_PATH = "pwa/zonas_atleta.json"


def carregar_zonas():
    if not os.path.exists(ZONAS_PATH): return {}
    try:
        with open(ZONAS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _faixa_zona(tabela, zona_txt):
    """Dado {'Z1':[a,b],'Z2':[c,d],...} e um texto tipo 'Z3' ou 'Z3-Z4',
    retorna [min, max] cobrindo todas as zonas citadas — ou None."""
    if not tabela or not zona_txt: return None
    nums = sorted(set(int(n) for n in re.findall(r"\d+", str(zona_txt))))
    if not nums: return None
    vals = []
    for n in nums:
        rng = tabela.get(f"Z{n}")
        if rng: vals.extend(rng)
    if not vals: return None
    return [min(vals), max(vals)]


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


def _zona_num(zona):
    """'Z4' → 4 | 'Z3-Z4' → 4 (pega a maior) | None se não der pra interpretar.
    OBS: não usado para alvo numérico no Garmin (ver nota em _passo) — só
    mantido para eventual uso futuro; usa regex, nunca concatena dígitos."""
    if not zona: return None
    nums = [int(n) for n in re.findall(r"\d+", str(zona))]
    return max(nums) if nums else None


def _passo(order, step_type_id, step_type_key, dur_min, zona, desc, esporte, zonas, modo_alvo):
    """modo_alvo: 'pace_power' (pace pra corrida / potência pra bike) |
    'hr' (frequência cardíaca) | 'nenhum' (só o texto da zona, sem alvo numérico)."""
    zona_txt = f" ({zona})" if zona else ""
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": step_type_id, "stepTypeKey": step_type_key},
        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
        "endConditionValue": int(round(dur_min * 60)),
        "description": ((desc or "") + zona_txt)[:120],
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }
    if modo_alvo == "nenhum" or not zona:
        return step

    if modo_alvo == "pace_power":
        if esporte == "bike":
            faixa = _faixa_zona(zonas.get("bike_zonas_w"), zona)
            if faixa:
                step["targetType"] = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone"}
                step["targetValueOne"], step["targetValueTwo"] = faixa
        elif esporte == "run":
            faixa = _faixa_zona(zonas.get("run_zonas_pace_s_km"), zona)
            if faixa:
                # Garmin quer velocidade (m/s) — pace MENOR (s/km) = mais rápido = velocidade MAIOR
                lo_s, hi_s = faixa
                v_lo, v_hi = 1000.0/hi_s, 1000.0/lo_s
                # Sequência confirmada até aqui: 1=nenhum, 2=potência✅, 3=cadência,
                # 4=FC✅, 5=velocidade (mostrou "Speed" em km/h, não pace de verdade).
                # Tentando 6 agora — próximo plausível para um alvo de PACE genuíno
                # (que os outros apps mostram nativamente em min/km, não em km/h).
                step["targetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
                step["targetValueOne"], step["targetValueTwo"] = round(v_lo, 2), round(v_hi, 2)
    elif modo_alvo == "hr":
        faixa = _faixa_zona(zonas.get("hr_zonas"), zona)
        if faixa:
            step["targetType"] = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.custom"}
            step["targetValueOne"], step["targetValueTwo"] = faixa
    return step


def _steps_estruturados(estrutura, esporte, zonas, modo_alvo):
    """Monta aquecimento/intervalado(repeat)/volta_calma a partir dos blocos
    que a IA gerou. Retorna None se a estrutura vier vazia/mal-formada —
    quem chama cai de volta no bloco único simples."""
    if not estrutura: return None
    steps, order = [], 1
    for bloco in estrutura:
        tipo = (bloco.get("bloco") or "").lower()
        if tipo == "aquecimento":
            steps.append(_passo(order, 1, "warmup", bloco.get("duracao_min", 10), bloco.get("zona"),
                                 "Aquecimento", esporte, zonas, modo_alvo))
            order += 1
        elif tipo == "volta_calma":
            steps.append(_passo(order, 2, "cooldown", bloco.get("duracao_min", 10), bloco.get("zona"),
                                 "Volta à calma", esporte, zonas, modo_alvo))
            order += 1
        elif tipo == "intervalado":
            reps = int(bloco.get("repeticoes") or 1)
            trabalho = _passo(1, 3, "interval", bloco.get("trabalho_min", 3), bloco.get("trabalho_zona"),
                               "Forte", esporte, zonas, modo_alvo)
            descanso = _passo(2, 4, "recovery", bloco.get("descanso_min", 1.5), bloco.get("descanso_zona"),
                               "Recuperação", esporte, zonas, modo_alvo)
            steps.append({
                "type": "RepeatGroupDTO",
                "stepOrder": order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                "numberOfIterations": reps,
                "smartRepeat": False,
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
                "workoutSteps": [trabalho, descanso],
            })
            order += 1
    return steps or None


def montar_payload(date_str, sessao, zonas=None, usar_estrutura=True, modo_alvo="nenhum"):
    esporte = sessao.get("esporte")
    sport   = SPORT_TYPE.get(esporte)
    if not sport:
        return None
    zonas = zonas or {}
    dur_min = int(sessao.get("duracao_min") or 30)
    nota = f"{sessao.get('zona','')} — {sessao.get('descricao','')}".strip(" —")[:250]
    nome = f"{SPORT_LABEL.get(esporte,esporte)} · {sessao.get('tipo','Treino')}"[:80]

    steps = None
    if usar_estrutura:
        steps = _steps_estruturados(sessao.get("estrutura"), esporte, zonas, modo_alvo)

    if not steps:
        # Sessão contínua (ou fallback): um único bloco pela duração total
        steps = [_passo(1, 3, "interval", dur_min, sessao.get("zona"), nota, esporte, zonas, modo_alvo)]

    return {
        "workoutName": nome,
        "description": f"TP Performance Coach · plano · {date_str}",
        "sportType": sport,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport,
            "workoutSteps": steps,
        }],
    }


def sync_dia(api, date_str, sessoes, ids_map, zonas=None):
    zonas = zonas or {}
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

        # 4 tentativas, cada vez mais simples — cai pro próximo nível só se o Garmin recusar:
        # 1) pace (corrida) / potência (bike) — o que o atleta prefere usar de verdade
        # 2) frequência cardíaca — plano B numérico, se pace/potência não colar
        # 3) zona só em texto (sem alvo) — sempre funciona
        # 4) bloco único (fallback final, se nem a estrutura em blocos for aceita)
        tentativas = [
            ("pace/potência",           dict(zonas=zonas, usar_estrutura=True,  modo_alvo="pace_power")),
            ("frequência cardíaca",     dict(zonas=zonas, usar_estrutura=True,  modo_alvo="hr")),
            ("zona em texto",           dict(zonas=zonas, usar_estrutura=True,  modo_alvo="nenhum")),
            ("bloco único (fallback)",  dict(zonas=zonas, usar_estrutura=False, modo_alvo="nenhum")),
        ]
        wid = None
        for nome_tentativa, kwargs in tentativas:
            payload = montar_payload(date_str, sessao, **kwargs)
            if not payload:
                break
            try:
                res = api.upload_workout(payload)
                wid = res.get("workoutId")
                if wid:
                    break
            except Exception as e:
                print(f"    ({nome_tentativa} falhou: {e})")
                continue
        if wid:
            try:
                api.schedule_workout(wid, date_str)
                ids_map[key] = wid
                criados += 1
                print(f"  ✅ {date_str} {SPORT_LABEL.get(esporte,esporte)}: workout {wid} agendado")
            except Exception as e:
                print(f"  ⚠️ {date_str} {esporte}: criado (id={wid}) mas falhou ao agendar: {e}")
        else:
            print(f"  ❌ Falha em {date_str} {esporte} — todas as tentativas falharam")
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

    zonas = carregar_zonas()
    print(f"  Zonas do atleta: {list(zonas.keys()) or 'nenhuma (rode buscar_zonas_garmin.py)'}")
    print(f"  {len(dias_alvo)} dia(s) para processar...")
    api = garmin_login()

    total = 0
    for dt in sorted(dias_alvo):
        total += sync_dia(api, dt, dias_alvo[dt], ids_map, zonas=zonas)

    with open(IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(ids_map, f, ensure_ascii=False, indent=2)

    print(f"  {total} treino(s) enviado(s) ao Garmin.")
    print("Concluído ✅")


if __name__ == "__main__":
    main()
