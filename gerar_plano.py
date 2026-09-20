#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera um plano base de 4 semanas (28 dias) de treino de triathlon,
respeitando parâmetros do atleta (horas/semana, dias disponíveis, dias
preferidos por esporte, dias longos, atividades fixas). Não depende do
Garmin — a revisão fina do dia-a-dia é feita pelo garmin_briefing.py,
que já roda diariamente com dados reais de recuperação.

Lê:  pwa/plano_config.json  (gerado pelo app)
Gera: pwa/plano.json        (consumido pelo app e pelo briefing diário)
"""

import os, json, datetime, urllib.request, urllib.error

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TODAY = datetime.date.today()

_race_env = os.environ.get("RACE_DATE", "")
try:    RACE_DATE = datetime.date.fromisoformat(_race_env) if _race_env else None
except: RACE_DATE = None

DOW_PT = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]  # weekday() 0=seg
DOW_LABEL = {"seg":"Segunda","ter":"Terça","qua":"Quarta","qui":"Quinta","sex":"Sexta","sab":"Sábado","dom":"Domingo"}

DEFAULT_CONFIG = {
    "tipo_prova": "triathlon",       # "triathlon" ou "corrida"
    "prova_categoria": "middle",     # sprint|olimpico|middle|long OU 5k|10k|12k|15k|21k|42k
    "foco_modalidade": "equilibrado",# equilibrado|swim|bike|run
    "horas_semana": 8,
    "dias_disponiveis": ["seg","ter","qua","qui","sex","sab","dom"],
    "dia_preferido": {"swim":"qua", "bike":"sab", "run":"ter"},
    "dia_longo": {"bike":"sab", "run":"dom"},
    "atividades_fixas": [],
}

CATEGORIA_LABEL = {
    "sprint":"Sprint Triathlon", "olimpico":"Triathlon Olímpico",
    "middle":"Ironman 70.3 (Middle)", "long":"Ironman Completo (Long)",
    "5k":"5K", "10k":"10K", "12k":"12K", "15k":"15K", "21k":"21K (Meia Maratona)", "42k":"42K (Maratona)",
}

def fase_do_dia(d):
    if not RACE_DATE: return "BASE"
    dias = (RACE_DATE - d).days
    if dias < 0:    return "PÓS-PROVA"
    if dias <= 7:   return "TAPER FINAL"
    if dias <= 21:  return "TAPER"
    if dias <= 42:  return "PEAK"
    if dias <= 84:  return "BUILD"
    return "BASE"

def carregar_config():
    path = "pwa/plano_config.json"
    if not os.path.exists(path):
        print("  ⚠️ plano_config.json não encontrado — usando defaults.")
        return dict(DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in cfg.items() if v not in (None, "", [])})
    return out

def montar_esqueleto(cfg):
    """28 dias a partir de hoje, com atividades fixas já encaixadas e
    marcações de dia disponível / dia longo / fase da temporada."""
    fixas_por_dow = {}
    for a in cfg.get("atividades_fixas", []):
        fixas_por_dow.setdefault(a["dia"], []).append(a)

    dias = {}
    for i in range(28):
        dt = TODAY + datetime.timedelta(days=i)
        dow = DOW_PT[dt.weekday()]
        dias[dt.isoformat()] = {
            "dow": dow,
            "disponivel": dow in cfg.get("dias_disponiveis", []),
            "longo_bike": cfg.get("dia_longo",{}).get("bike") == dow,
            "longo_run":  cfg.get("dia_longo",{}).get("run")  == dow,
            "fase": fase_do_dia(dt),
            "fixos": [
                {"esporte": a.get("esporte","outro"), "nome": a.get("nome",""),
                 "duracao_min": a.get("duracao_min", 60)}
                for a in fixas_por_dow.get(dow, [])
            ],
            "sessoes": [],  # preenchido pela IA (não-fixas) + fixos abaixo
        }
    return dias

def montar_prompt(cfg, esqueleto):
    semanas_resumo = []
    dts = sorted(esqueleto.keys())
    for w in range(4):
        semana_dias = dts[w*7:(w+1)*7]
        fase = esqueleto[semana_dias[0]]["fase"]
        semanas_resumo.append(f"Semana {w+1} ({semana_dias[0]} a {semana_dias[-1]}): fase {fase}")

    linhas_dias = []
    for dt in dts:
        info = esqueleto[dt]
        fixos_txt = "; ".join(f"{f['esporte']} '{f['nome']}' {f['duracao_min']}min" for f in info["fixos"]) or "nenhuma"
        marcas = []
        if info["longo_bike"]: marcas.append("DIA LONGO DE BIKE")
        if info["longo_run"]:  marcas.append("DIA LONGO DE CORRIDA")
        if not info["disponivel"]: marcas.append("DIA INDISPONÍVEL PARA ENDURANCE")
        linhas_dias.append(
            f"{dt} ({DOW_LABEL[info['dow']]}, {info['fase']}): fixo=[{fixos_txt}]"
            + (f" · {' + '.join(marcas)}" if marcas else "")
        )

    tipo_prova = cfg.get("tipo_prova", "triathlon")
    categoria  = cfg.get("prova_categoria", "middle")
    categoria_label = CATEGORIA_LABEL.get(categoria, categoria)
    foco = cfg.get("foco_modalidade", "equilibrado")

    if tipo_prova == "corrida":
        objetivo_txt = f"""OBJETIVO: prova de CORRIDA DE RUA — {categoria_label}.
Este plano deve ser MAJORITARIAMENTE ou EXCLUSIVAMENTE de corrida. Só inclua natação/bike se
o atleta tiver marcado atividades fixas dessas modalidades — nesse caso, mantenha-as como estão,
mas não crie sessões adicionais de natação/bike geradas por você.
Diretrizes por distância:
- 5K/10K: ênfase em VO2max (intervalados curtos/médios), tempo run, long runs moderados.
- 12K/15K: base aeróbica + tempo run + long runs progressivos.
- 21K (Meia): volume semanal consistente, long runs progressivos até ~18-19km, 1 tempo run/semana.
- 42K (Maratona): maior volume semanal do plano, long runs progressivos até ~30-32km na semana 3, ritmo de maratona nos treinos-chave."""
    else:
        objetivo_txt = f"""OBJETIVO: TRIATHLON — {categoria_label}.
Distribua o volume entre natação, bike e corrida de forma equilibrada (ajustada pelo foco abaixo).
Diretrizes por categoria:
- Sprint: sessões mais curtas e intensas, ênfase em técnica e transições, menor volume total.
- Olímpico: volume moderado, mix de intensidade e resistência equilibrado entre os 3 esportes.
- Middle (70.3): forte ênfase em resistência aeróbica, treinos longos de bike e corrida, alguma sessão "brick" (bike+corrida seguidas).
- Long (Ironman): volume alto especialmente em bike, long rides muito extensos, long runs consistentes, foco quase total em resistência aeróbica."""

    foco_txt = ""
    if foco != "equilibrado" and not (tipo_prova=="corrida" and foco!="run"):
        foco_label = {"swim":"NATAÇÃO","bike":"BIKE","run":"CORRIDA"}.get(foco, foco.upper())
        foco_txt = f"""
FOCO DE MELHORIA: o atleta quer priorizar {foco_label}. Direcione ~45-55% do volume semanal de
triatlo para essa modalidade (mais sessões e/ou sessões mais longas/intensas), mantendo apenas o
mínimo técnico necessário nas outras duas para não perder economia de movimento nem gerar overuse."""

    prompt = f"""Você é um treinador especializado, montando um PLANO BASE de 4 semanas (periodização).

{objetivo_txt}{foco_txt}

PARÂMETROS DO ATLETA:
- Volume alvo: ~{cfg.get('horas_semana')}h de treino por semana (SEM contar atividades fixas como futebol)
- Dias disponíveis para treino: {', '.join(cfg.get('dias_disponiveis', []))}
- Dia preferido por esporte: natação={cfg.get('dia_preferido',{}).get('swim','livre')}, bike={cfg.get('dia_preferido',{}).get('bike','livre')}, corrida={cfg.get('dia_preferido',{}).get('run','livre')}
- Dia do treino longo: bike={cfg.get('dia_longo',{}).get('bike','não definido')}, corrida={cfg.get('dia_longo',{}).get('run','não definido')}

RESUMO DAS 4 SEMANAS:
{chr(10).join(semanas_resumo)}

CALENDÁRIO COMPLETO (28 dias) — atividades fixas já ocupam esses dias, não sobrecarregue:
{chr(10).join(linhas_dias)}

REGRAS DE PERIODIZAÇÃO:
1. Progressão de carga: semanas 1-2 acumulação gradual, semana 3 pico de volume, semana 4 recuperação (redução de ~30-40% do volume) — modelo clássico 3:1.
2. Respeite a fase indicada em cada semana (BASE = volume/técnica, BUILD = intensidade+volume, PEAK = pico específico, TAPER = redução drástica mantendo intensidade).
3. Em dias com atividade fixa, NÃO adicione outra sessão do mesmo grupo muscular/esporte pesada — no máximo uma sessão leve complementar ou nenhuma.
4. Em "DIA INDISPONÍVEL", não agende nenhuma sessão de triatlo.
5. Inclua pelo menos 1 dia de descanso completo (sem nenhuma sessão) por semana.
6. Distribua natação, bike e corrida ao longo da semana com equilíbrio, priorizando os dias preferidos quando possível.
7. Sessões de "DIA LONGO" devem ter a maior duração da semana para aquele esporte.
8. Duração de cada sessão em minutos, realista e coerente com o volume semanal total.

Responda SOMENTE em JSON válido, sem markdown, no formato:
{{
  "dias": {{
    "2026-09-20": {{"sessoes": [
      {{"esporte": "swim|bike|run", "tipo": "Nome do treino (ex: Intervalado 6x400m)", "duracao_min": 60, "zona": "Z2", "descricao": "1 frase de execução", "longo": false}}
    ]}},
    "...": {{"sessoes": [...]}}
  }}
}}
Inclua TODAS as 28 datas do calendário acima como chaves, mesmo que "sessoes" seja uma lista vazia (dia de descanso ou indisponível).
Nenhum texto fora do JSON."""
    return prompt

def chamar_ia(prompt):
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        result = json.loads(r.read())
    raw = result["content"][0]["text"].strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def mesclar(esqueleto, ia_dias):
    for dt, info in esqueleto.items():
        sessoes = []
        for f in info["fixos"]:
            sessoes.append({"esporte": f["esporte"], "tipo": f["nome"], "duracao_min": f["duracao_min"],
                             "zona": "—", "descricao": "Atividade fixa", "longo": False, "fixo": True})
        ia_info = (ia_dias or {}).get(dt, {})
        for sess in ia_info.get("sessoes", []):
            sess["fixo"] = False
            sessoes.append(sess)
        info["sessoes"] = sessoes
    return esqueleto

def main():
    print(f"[{TODAY.isoformat()}] Gerando plano base de 4 semanas...")
    cfg = carregar_config()
    cat_label = CATEGORIA_LABEL.get(cfg.get('prova_categoria',''), cfg.get('prova_categoria',''))
    print(f"  Objetivo: {cfg.get('tipo_prova','triathlon').upper()} — {cat_label} · Foco: {cfg.get('foco_modalidade','equilibrado')}")
    print(f"  Config: {cfg.get('horas_semana')}h/semana · dias: {cfg.get('dias_disponiveis')}")
    print(f"  Atividades fixas: {len(cfg.get('atividades_fixas', []))}")

    esqueleto = montar_esqueleto(cfg)
    prompt = montar_prompt(cfg, esqueleto)

    print("  Consultando IA para preencher as sessões de treino...")
    try:
        ia_resp = chamar_ia(prompt)
    except urllib.error.HTTPError as e:
        print(f"❌ Erro API {e.code}: {e.read().decode()[:400]}")
        raise SystemExit(1)
    except Exception as e:
        print(f"❌ Erro ao gerar plano: {e}")
        raise SystemExit(1)

    dias_final = mesclar(esqueleto, ia_resp.get("dias", {}))

    semanas = []
    dts = sorted(dias_final.keys())
    for w in range(4):
        semana_dts = dts[w*7:(w+1)*7]
        semanas.append({
            "numero": w + 1,
            "fase": dias_final[semana_dts[0]]["fase"],
            "inicio": semana_dts[0],
            "fim": semana_dts[-1],
            "dias": {dt: dias_final[dt] for dt in semana_dts},
        })

    plano = {
        "gerado_em": datetime.datetime.utcnow().isoformat() + "Z",
        "semana_inicio": dts[0],
        "semana_fim": dts[-1],
        "config_usada": cfg,
        "semanas": semanas,
    }

    os.makedirs("pwa", exist_ok=True)
    with open("pwa/plano.json", "w", encoding="utf-8") as f:
        json.dump(plano, f, ensure_ascii=False, default=str, indent=2)
    print("  ✅ plano.json salvo → pwa/plano.json")
    print("Concluído ✅")

if __name__ == "__main__":
    main()
