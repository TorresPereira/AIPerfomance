#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exporta o treino de academia (A/B/C) do report.json para o Garmin Connect
como workout de strength_training, agendado para hoje (aparece no relógio)."""

import os, json, datetime, re
from garminconnect import Garmin

GARMIN_EMAIL    = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
DIA             = (os.environ.get("DIA") or "").upper().strip()
CACHE_DIR       = "/tmp/garmin_cache"
TODAY           = datetime.date.today()

# ── Mapeamento PT → categoria de exercício Garmin ─────────────────────────────
CAT_MAP = [
    (r"supino|bench|press de peito",              "BENCH_PRESS"),
    (r"crucifixo|fly|peck|voador",                "FLYE"),
    (r"agacham|squat|hack",                       "SQUAT"),
    (r"leg press",                                "SQUAT"),
    (r"terra|deadlift|stiff|rdl|romeno",          "DEADLIFT"),
    (r"remada|row",                               "ROW"),
    (r"puxada|pulldown|pull.?up|barra fixa",      "PULL_UP"),
    (r"desenvolvimento|shoulder press|militar|arnold", "SHOULDER_PRESS"),
    (r"eleva[cç][aã]o lateral|lateral raise",     "LATERAL_RAISE"),
    (r"eleva[cç][aã]o frontal",                   "LATERAL_RAISE"),
    (r"rosca|curl de b|b[ií]ceps",                "CURL"),
    (r"tr[ií]ceps|testa|franc[eê]s|corda|pushdown","TRICEPS_EXTENSION"),
    (r"afundo|lunge|b[uú]lgaro|passada",          "LUNGE"),
    (r"flexora|leg curl",                         "LEG_CURL"),
    (r"extensora|leg extension",                  "SQUAT"),
    (r"panturrilha|calf",                         "CALF_RAISE"),
    (r"prancha|plank",                            "PLANK"),
    (r"abdominal|crunch|abd[oô]men",              "CRUNCH"),
    (r"gl[uú]teo|hip thrust|ponte|eleva[cç][aã]o p[eé]lvica", "HIP_RAISE"),
    (r"flex[aã]o|push.?up",                       "PUSH_UP"),
]

# Categorias válidas na taxonomia FIT do Garmin (categoria inválida → HTTP 400)
CATS_VALIDAS = {
    "BENCH_PRESS","CALF_RAISE","CARDIO","CARRY","CHOP","CORE","CRUNCH","CURL",
    "DEADLIFT","FLYE","HIP_EXTENSION","HIP_RAISE","HIP_STABILITY","HYPEREXTENSION",
    "LATERAL_RAISE","LEG_CURL","LEG_RAISE","LUNGE","OLYMPIC_LIFT","PLANK","PLYO",
    "PULL_UP","PUSH_UP","ROW","SHOULDER_PRESS","SHOULDER_STABILITY","SHRUG",
    "SIT_UP","SQUAT","TOTAL_BODY","TRICEPS_EXTENSION","WARM_UP",
}

def garmin_cat(nome):
    n = (nome or "").lower()
    for pat, cat in CAT_MAP:
        if re.search(pat, n):
            return cat if cat in CATS_VALIDAS else "TOTAL_BODY"
    return "TOTAL_BODY"

def parse_reps(rep_str):
    """'8-12' → 10 | '30s' → 30 | '12' → 12"""
    s = str(rep_str or "10")
    m = re.findall(r"\d+", s)
    if not m: return 10
    if len(m) >= 2: return (int(m[0]) + int(m[1])) // 2
    return int(m[0])

def montar_workout(dia, treino):
    """Monta o payload do Garmin workout-service para strength_training."""
    steps, order = [], 1
    for ex in treino.get("exercicios", []):
        series = int(ex.get("series") or 3)
        reps   = parse_reps(ex.get("repeticoes"))
        cat    = garmin_cat(ex.get("exercicio"))

        exercise_step = {
            "type": "ExecutableStepDTO",
            "stepOrder": order + 1,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps"},
            "endConditionValue": reps,
            "category": cat,
            "exerciseName": None,
            "description": (ex.get("exercicio") or "")[:80],
        }
        rest_step = {
            "type": "ExecutableStepDTO",
            "stepOrder": order + 2,
            "stepType": {"stepTypeId": 5, "stepTypeKey": "rest"},
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": 90,
        }
        steps.append({
            "type": "RepeatGroupDTO",
            "stepOrder": order,
            "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
            "numberOfIterations": series,
            "smartRepeat": False,
            "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
            "workoutSteps": [exercise_step, rest_step],
        })
        order += 3

    return {
        "workoutName": f"Gym {dia} · {treino.get('grupo','')[:40]}",
        "description": f"TP Performance Coach · gerado {TODAY.isoformat()}",
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
            "workoutSteps": steps,
        }],
    }

def main():
    print(f"[{TODAY}] Exportando treino de academia para o Garmin...")

    # ── Carrega report.json ──
    with open("pwa/report.json", encoding="utf-8") as f:
        report = json.load(f)
    g = (report.get("insights") or {}).get("treino_academia") or {}
    treinos = g.get("treinos") or {}
    if not treinos and g.get("exercicios"):
        treinos = {(g.get("dia") or "A"): {"grupo": g.get("grupo",""), "exercicios": g["exercicios"]}}

    dia = DIA if DIA in treinos else (g.get("dia_hoje") or g.get("dia") or "A").upper()
    treino = treinos.get(dia)
    if not treino or not treino.get("exercicios"):
        print(f"❌ Treino '{dia}' não encontrado no report.json")
        print(f"   Chaves disponíveis: {list(treinos.keys()) or 'nenhuma'}")
        print("   Rode o briefing principal primeiro para gerar os treinos ABC.")
        import sys; sys.exit(1)

    print(f"  Dia {dia}: {treino.get('grupo')} — {len(treino['exercicios'])} exercícios")

    # ── Login Garmin (sessão cacheada + retry em rate limit) ──
    import time
    os.makedirs(CACHE_DIR, exist_ok=True)
    api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    logado = False
    try:
        api.login(tokenstore=CACHE_DIR)
        print("  Sessão restaurada."); logado = True
    except Exception as e1:
        print(f"  Sessão inválida ({e1}) — tentando login novo...")
        for tentativa in range(3):
            try:
                api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
                api.login()
                try: api.garth.dump(CACHE_DIR)
                except Exception: pass
                print("  Novo login efetuado."); logado = True; break
            except Exception as e2:
                if "429" in str(e2) or "Too Many" in str(e2):
                    espera = 60 * (tentativa + 1)
                    print(f"  ⏳ Rate limit do Garmin — aguardando {espera}s (tentativa {tentativa+1}/3)...")
                    time.sleep(espera)
                else:
                    raise
    if not logado:
        print("❌ Garmin bloqueou logins deste IP (429). O bloqueio expira sozinho.")
        print("   Aguarde 30-60 min e tente de novo — ou espere o briefing das 08:00")
        print("   renovar a sessão em cache; depois disso o export usa a sessão pronta.")
        import sys; sys.exit(1)

    # ── Cria o workout ──
    payload = montar_workout(dia, treino)
    def _post_workout(pl):
        resp = api.garth.post("connectapi", "/workout-service/workout",
                              json=pl, api=True)
        return resp.json() if hasattr(resp, "json") else resp

    try:
        res = _post_workout(payload)
    except Exception as e:
        print(f"  ⚠️ 1ª tentativa falhou: {e}")
        print("  Payload enviado (início):")
        print("  " + json.dumps(payload, ensure_ascii=False)[:1200])
        # Fallback: neutraliza categorias (algum nome pode ser inválido)
        try:
            for st in payload["workoutSegments"][0]["workoutSteps"]:
                for sub in st.get("workoutSteps", []):
                    if "category" in sub: sub["category"] = "TOTAL_BODY"
            res = _post_workout(payload)
            print("  ✅ Criado no fallback (categorias genéricas)")
        except Exception as e2:
            import traceback; traceback.print_exc()
            print(f"❌ Falha ao criar workout: {e2}")
            import sys; sys.exit(1)

    wid = (res or {}).get("workoutId")
    if not wid:
        print(f"❌ Resposta sem workoutId: {json.dumps(res, default=str)[:500]}")
        import sys; sys.exit(1)
    print(f"  ✅ Workout criado: id={wid}")

    # ── Agenda para hoje (aparece no calendário do relógio) ──
    try:
        api.garth.post("connectapi", f"/workout-service/schedule/{wid}",
                       json={"date": TODAY.isoformat()}, api=True)
        print(f"  ✅ Agendado para {TODAY.isoformat()} — sincronize o relógio")
    except Exception as e:
        print(f"  ⚠️ Workout criado mas não agendado: {e}")

    print("Concluído ✅")

if __name__ == "__main__":
    main()
