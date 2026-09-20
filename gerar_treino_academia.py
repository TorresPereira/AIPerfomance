#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera o treino de academia (A/B/C rotação + Força) SOB DEMANDA — não roda
mais todo dia junto com o briefing, só quando o atleta pede (botão "Gerar
Novo Treino" na aba Gym do app).

Antes de sobrescrever, guarda o treino atual de cada dia no histórico
(pwa/treino_academia.json → "historico"), pra dar pra comparar progressão.

Não precisa de login no Garmin — só lê o último report.json (se existir)
pra saber o readiness do dia, e chama a IA. Por isso roda em GitHub Actions
sem risco de bloqueio.
"""

import os, json, datetime, urllib.request, urllib.error

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TODAY = datetime.date.today()
HIST_MAX = 3  # quantas gerações anteriores guardar por dia (A/B/C/F)

def readiness_atual():
    """Lê o último report.json gerado (se existir) pra adaptar o treino
    ao estado de hoje. Sem isso, usa um meio-termo neutro."""
    try:
        with open("pwa/report.json", encoding="utf-8") as f:
            r = json.load(f)
        s = r.get("saude", {})
        return {
            "body_battery": s.get("body_battery", 55),
            "training_status": s.get("training_status", "—"),
            "hrv": s.get("hrv", "—"),
        }
    except Exception:
        return {"body_battery": 55, "training_status": "—", "hrv": "—"}

def montar_prompt(r):
    dia_sugerido = "ABC"[TODAY.toordinal() % 3]
    return f"""Você é um treinador de força para triatleta de Half Ironman 70.3.

ESTADO ATUAL DO ATLETA:
- Body Battery: {r['body_battery']}
- Status de treino: {r['training_status']}
- HRV: {r['hrv']}
- Dia sugerido da rotação hoje: {dia_sugerido} (A=Peito/Ombro/Tríceps, B=Costas/Bíceps, C=Pernas/Glúteos/Core)

Gere os TRÊS treinos de academia (A, B, C) com APARELHOS (máquinas, cabos, halteres, barras),
mais uma sugestão de treino funcional específico para triatlo (F).

Responda SOMENTE em JSON válido, sem markdown:
{{
  "dia_hoje": "{dia_sugerido}",
  "treinos": {{
    "A": {{"grupo": "Peito, Ombro e Tríceps", "exercicios": [
      {{"exercicio": "Nome com o aparelho", "series": 4, "repeticoes": "8-12", "carga": "moderada", "musculo": "Peito", "obs": "Dica de execução em 1 frase"}}
    ]}},
    "B": {{"grupo": "Costas e Bíceps", "exercicios": [...]}},
    "C": {{"grupo": "Pernas, Glúteos e Core", "exercicios": [...]}}
  }},
  "forca": [
    {{"exercicio": "Nome do exercício funcional", "series": 3, "repeticoes": "10-12", "carga": "moderada", "foco": "Por que este exercício ajuda no triatlo"}}
  ]
}}

Regras:
- A, B, C: 5 a 7 exercícios cada, COM APARELHOS, do composto pro isolado, terminando em 1 exercício de core
- Ajuste séries/reps/carga ao Body Battery: <40 = 3x12-15 leve | 40-70 = 3-4x10-12 moderada | >70 = 4x6-10 pesada
- "forca": 5 a 7 exercícios funcionais (sem aparelho pesado obrigatório) para triatlo — mobilidade, estabilidade, core, glúteos
- "musculo" só nos treinos A/B/C; "foco" só em "forca"
- carga: "leve", "moderada" ou "pesada"
Nenhum texto fora do JSON."""

def chamar_ia(prompt):
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 6000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())
    raw = result["content"][0]["text"].strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def main():
    print(f"[{TODAY.isoformat()}] Gerando treino de academia sob demanda...")
    r = readiness_atual()
    print(f"  Readiness: BB={r['body_battery']} · {r['training_status']}")

    try:
        novo = chamar_ia(montar_prompt(r))
    except urllib.error.HTTPError as e:
        print(f"❌ Erro API {e.code}: {e.read().decode()[:400]}")
        raise SystemExit(1)
    except Exception as e:
        print(f"❌ Erro ao gerar treino: {e}")
        raise SystemExit(1)

    # Carrega o arquivo atual (se existir) pra arquivar no histórico antes de sobrescrever
    os.makedirs("pwa", exist_ok=True)
    path = "pwa/treino_academia.json"
    atual = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                atual = json.load(f)
        except Exception:
            atual = {}

    historico = atual.get("historico", {})
    treinos_antigos = atual.get("treinos", {})
    forca_antigo = atual.get("forca")
    gerado_em_antigo = atual.get("gerado_em")

    for letra, treino in treinos_antigos.items():
        if treino and treino.get("exercicios"):
            historico.setdefault(letra, []).insert(0, {"gerado_em": gerado_em_antigo, **treino})
            historico[letra] = historico[letra][:HIST_MAX]
    if forca_antigo:
        historico.setdefault("F", []).insert(0, {"gerado_em": gerado_em_antigo, "grupo": "Força Funcional", "exercicios": forca_antigo})
        historico["F"] = historico["F"][:HIST_MAX]

    saida = {
        "gerado_em": datetime.datetime.utcnow().isoformat() + "Z",
        "dia_hoje": novo.get("dia_hoje"),
        "treinos": novo.get("treinos", {}),
        "forca": novo.get("forca", []),
        "historico": historico,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, default=str, indent=2)
    print(f"  💾 {path} salvo — dia sugerido: {saida['dia_hoje']}")
    print("Concluído ✅")

if __name__ == "__main__":
    main()
