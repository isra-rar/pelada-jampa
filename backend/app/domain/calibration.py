"""Calibração do sorteio com os placares reais (PRD §9.4 e §21, v2.0).

Cada jogo registrado entre dois times de um sorteio publicado vira uma amostra: de um lado, as diferenças que
o sorteio deixou entre os dois times em cada critério da função de custo (média, estrelas, atributos e
habilidades); do outro, o placar. A pergunta é: o sorteio gera jogos parelhos? E qual desequilíbrio que sobrou
mais se traduz em goleada?

- Indicadores: jogos decididos por até 1 gol, empates, goleadas (3+ gols), saldo médio e quanto o time
  "favorito" do sorteio (maior média) de fato vence.
- Para cada critério, a correlação entre a diferença deixada pelo sorteio e o saldo de gols (em módulo).
  Correlação positiva = quando esse critério fica desequilibrado, o jogo tende a ficar desequilibrado.
- Pesos sugeridos: os atuais (normalizados) misturados com a importância medida pelas correlações. A mistura
  cresce com a quantidade de jogos (até 50%), para poucos dados não mudarem muito o sorteio.

Componente de domínio puro: recebe as estatísticas dos times já calculadas (como ficam salvas no sorteio).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean

from app.domain.rating import FIELD_ATTRIBUTES, stars_of

CRITERIA: tuple[str, ...] = ("average", "stars", "attributes", "skills")
MIN_GAMES = 8  # abaixo disso não há sugestão de pesos
MAX_BLEND = 0.5  # quanto, no máximo, a medição pesa na sugestão
BLEND_FULL_AT = 60  # com esta quantidade de jogos a mistura chega ao máximo
MIN_WEIGHT = 0.05  # nenhum critério some do sorteio
CLOSE_GOALS = 1
BLOWOUT_GOALS = 3


@dataclass(frozen=True)
class GameSample:
    home_stats: dict
    away_stats: dict
    home_score: int
    away_score: int


def _stars_mean(stats: dict) -> float:
    """Média de estrelas do time. Sorteios antigos guardaram a contagem por Tier (letras) em "tiers"."""
    counts = stats.get("stars") or stats.get("tiers") or {}
    total = sum(counts.values())
    return sum(stars_of(level) * c for level, c in counts.items()) / total if total else 0.0


def components(a: dict, b: dict) -> dict[str, float]:
    """Diferença entre dois times em cada critério do sorteio (sempre >= 0)."""
    attrs_a, attrs_b = a.get("attributes") or {}, b.get("attributes") or {}
    skills_a, skills_b = a.get("skills") or {}, b.get("skills") or {}
    skill_keys = set(skills_a) | set(skills_b)
    return {
        "average": abs(float(a.get("average", 0.0)) - float(b.get("average", 0.0))),
        "stars": abs(_stars_mean(a) - _stars_mean(b)),
        "attributes": mean(abs(float(attrs_a.get(k, 0.0)) - float(attrs_b.get(k, 0.0))) for k in FIELD_ATTRIBUTES),
        "skills": (
            sum(abs(skills_a.get(k, 0) - skills_b.get(k, 0)) for k in skill_keys) / len(skill_keys)
            if skill_keys else 0.0
        ),
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sx = sqrt(sum((x - mx) ** 2 for x in xs))
    sy = sqrt(sum((y - my) ** 2 for y in ys))
    if sx < 1e-12 or sy < 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(weights.get(k, 0.0))) for k in CRITERIA}
    total = sum(clean.values())
    return {k: v / total for k, v in clean.items()} if total > 0 else {k: 1 / len(CRITERIA) for k in CRITERIA}


def _round_to_one(weights: dict[str, float]) -> dict[str, float]:
    """Arredonda para 2 casas mantendo a soma em 1,00 (a sobra vai para o maior peso)."""
    rounded = {k: round(v, 2) for k, v in weights.items()}
    drift = round(1.0 - sum(rounded.values()), 2)
    if drift:
        top = max(rounded, key=lambda k: rounded[k])
        rounded[top] = round(rounded[top] + drift, 2)
    return rounded


def _rate(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


def verdict(close_rate: float | None, games: int) -> tuple[str, str]:
    if not games or close_rate is None:
        return "sem_dados", "Ainda não há jogos registrados com times do sorteio."
    pct = f"{round(close_rate * 100)}%"
    if games < MIN_GAMES:
        return "poucos_dados", f"{games} jogo(s) até agora ({pct} parelhos). Registre mais placares para calibrar."
    if close_rate >= 0.6:
        return "bom", f"Os sorteios estão gerando jogos parelhos: {pct} decididos por até 1 gol."
    if close_rate >= 0.4:
        return "regular", f"Equilíbrio razoável: {pct} dos jogos decididos por até 1 gol."
    return "ruim", f"Muitos jogos desequilibrados: só {pct} decididos por até 1 gol. Vale ajustar os pesos."


def analyze(samples: list[GameSample], current_weights: dict[str, float]) -> dict:
    n = len(samples)
    current = _normalize(current_weights)
    goal_diffs = [abs(s.home_score - s.away_score) for s in samples]
    comps = [components(s.home_stats, s.away_stats) for s in samples]

    # favorito = time com maior média no sorteio; só conta jogo com vencedor e favorito claro
    decided = favorite_wins = 0
    for s in samples:
        gap = float(s.home_stats.get("average", 0.0)) - float(s.away_stats.get("average", 0.0))
        if s.home_score == s.away_score or abs(gap) < 0.05:
            continue
        decided += 1
        if (gap > 0) == (s.home_score > s.away_score):
            favorite_wins += 1

    correlations = {k: pearson([c[k] for c in comps], [float(g) for g in goal_diffs]) for k in CRITERIA}
    close = sum(1 for g in goal_diffs if g <= CLOSE_GOALS)
    status, message = verdict(_rate(close, n), n)

    suggested = None
    blend = 0.0
    signal = {k: max(0.0, r or 0.0) for k, r in correlations.items()}
    if n >= MIN_GAMES and sum(signal.values()) > 0:
        blend = min(MAX_BLEND, MAX_BLEND * n / BLEND_FULL_AT)
        measured = _normalize(signal)
        mixed = {k: (1 - blend) * current[k] + blend * measured[k] for k in CRITERIA}
        mixed = _normalize({k: max(MIN_WEIGHT, v) for k, v in mixed.items()})
        suggested = _round_to_one(mixed)

    return {
        "games": n,
        "close_games": close,
        "close_game_rate": _rate(close, n),
        "draw_rate": _rate(sum(1 for g in goal_diffs if g == 0), n),
        "blowout_rate": _rate(sum(1 for g in goal_diffs if g >= BLOWOUT_GOALS), n),
        "avg_goal_difference": round(mean(goal_diffs), 2) if goal_diffs else None,
        "favorite_games": decided,
        "favorite_win_rate": _rate(favorite_wins, decided),
        "avg_components": {k: round(mean(c[k] for c in comps), 4) if comps else None for k in CRITERIA},
        "correlations": {k: (round(v, 3) if v is not None else None) for k, v in correlations.items()},
        "current_weights": _round_to_one(current),
        "suggested_weights": suggested,
        "blend": round(blend, 2),
        "min_games": MIN_GAMES,
        "status": status,
        "message": message,
    }
