"""Sorteio inteligente de times (PRD §9).

Componente de domínio isolado: não conhece banco de dados nem HTTP.

Estratégia:
1. Elegibilidade: separa jogadores de linha e goleiros, valida quantidade, define reservas.
2. Construção aleatorizada por nível em estrelas: 5 e 4 estrelas primeiro, cada jogador vai para o time com
   menos jogadores daquele nível e menor força acumulada (desempate aleatório).
3. Busca local (hill climbing por trocas) minimizando a função de custo ponderada.
4. Repete enquanto houver tempo / orçamento de candidatos, guardando composições distintas.
5. Sorteia uma composição entre as de custo semelhante ao melhor (aleatoriedade controlada),
   evitando, se possível, repetir composições de sorteios anteriores.
6. Goleiros são distribuídos separadamente, por força relativa (o melhor goleiro vai para o
   time de linha mais fraco).
"""

from __future__ import annotations

import itertools
import random
import time
from dataclasses import dataclass, field
from statistics import mean

from app.domain.rating import FIELD_ATTRIBUTES, MAX_STARS, STAR_LEVELS


class DrawError(ValueError):
    pass


DEFAULT_WEIGHTS = {"average": 0.40, "stars": 0.25, "attributes": 0.20, "skills": 0.15}
SIZE_PENALTY = 10.0
# Escalas de referência: cada componente vale 1.0 quando atinge a diferença considerada "relevante".
# Assim os pesos do PRD (40/25/20/15) comparam grandezas equivalentes.
AVERAGE_SCALE = 0.25  # 0,25 ponto de diferença na média dos times
ATTRIBUTE_SCALE = 0.5  # 0,5 ponto de diferença média por atributo
STARS_SCALE = 1.0  # um jogador 5 estrelas "sobrando" em um time
SKILL_SCALE = 1.0  # uma habilidade concentrada além do mínimo possível


@dataclass(frozen=True)
class DrawPlayer:
    id: int
    average: float
    stars: int
    attributes: dict[str, float]
    skill: str | None = None
    provisional: bool = False


@dataclass(frozen=True)
class DrawGoalkeeper:
    id: int
    average: float
    stars: int
    provisional: bool = False
    # perfil de linha, usado se houver goleiros sobrando e ele precisar jogar na linha
    field_profile: DrawPlayer | None = None


@dataclass
class DrawConfig:
    num_teams: int = 2
    field_per_team: int = 5
    goalkeepers_per_team: int = 1
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    time_limit_seconds: float = 2.0
    max_candidates: int = 300
    relative_tolerance: float = 0.05
    absolute_tolerance: float = 0.02
    seed: int | None = None
    avoid_signatures: set[str] = field(default_factory=set)


@dataclass
class TeamResult:
    index: int
    field_ids: list[int]
    goalkeeper_ids: list[int]
    stats: dict


@dataclass
class DrawResult:
    teams: list[TeamResult]
    reserves: list[int]
    score: float
    metrics: dict
    warnings: list[str]
    seed: int
    signature: str
    candidates_evaluated: int
    fallback_used: bool


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Pesos salvos antes das estrelas usavam a chave "tier" para o critério de nível."""
    if "tier" not in weights:
        return weights
    out = {k: v for k, v in weights.items() if k != "tier"}
    out.setdefault("stars", weights["tier"])
    return out


# --------------------------------------------------------------------------- custo


def _range(values: list[float]) -> float:
    return (max(values) - min(values)) if values else 0.0


def _min_range(total: int, teams: int) -> int:
    return 0 if total % teams == 0 else 1


def evaluate(teams: list[list[DrawPlayer]], weights: dict[str, float]) -> tuple[float, dict]:
    """Função objetivo. Quanto menor, mais equilibrado."""
    t = len(teams)
    sizes = [len(team) for team in teams]
    total_players = sum(sizes)
    means = [mean(p.average for p in team) if team else 0.0 for team in teams]
    strengths = [sum(p.average for p in team) for team in teams]
    average_range = _range(means)

    # Estrelas: excesso de concentração de cada nível em relação ao mínimo matematicamente possível
    # (concentrar jogadores de nível alto pesa mais; 0 estrela pesa como 1 para não ser ignorado)
    star_counts = {n: [sum(1 for p in team if p.stars == n) for team in teams] for n in STAR_LEVELS}
    star_excess = 0.0
    for n, counts in star_counts.items():
        excess = max(0, (max(counts) - min(counts)) - _min_range(sum(counts), t))
        star_excess += max(1, n) * excess
    star_means = [mean(p.stars for p in team) if team else 0.0 for team in teams]
    stars_cost = star_excess / MAX_STARS + 0.5 * _range(star_means)

    # Atributos: diferença média por atributo
    attr_ranges = {}
    for key in FIELD_ATTRIBUTES:
        attr_means = [mean(p.attributes.get(key, 0.0) for p in team) if team else 0.0 for team in teams]
        attr_ranges[key] = _range(attr_means)
    attr_cost = mean(attr_ranges.values())

    # Habilidades especiais: concentração acima do mínimo possível
    skills = sorted({p.skill for team in teams for p in team if p.skill})
    skill_excess = 0
    for skill in skills:
        counts = [sum(1 for p in team if p.skill == skill) for team in teams]
        skill_excess += max(0, (max(counts) - min(counts)) - _min_range(sum(counts), t))
    skill_cost = skill_excess / len(skills) if skills else 0.0

    size_cost = max(0, (max(sizes) - min(sizes)) - _min_range(total_players, t)) * SIZE_PENALTY

    components = {
        "average": average_range / AVERAGE_SCALE,
        "stars": stars_cost / STARS_SCALE,
        "attributes": attr_cost / ATTRIBUTE_SCALE,
        "skills": skill_cost / SKILL_SCALE,
    }
    weights = normalize_weights(weights)
    cost = sum(weights.get(k, 0.0) * v for k, v in components.items()) + size_cost
    metrics = {
        "components": {k: round(v, 4) for k, v in components.items()},
        "size_penalty": size_cost,
        "average_difference": round(average_range, 4),
        "strength_difference": round(_range(strengths), 4),
        "attribute_differences": {k: round(v, 4) for k, v in attr_ranges.items()},
        "stars_distribution": {str(n): counts for n, counts in star_counts.items()},
    }
    return cost, metrics


def team_stats(team: list[DrawPlayer], goalkeepers: list[DrawGoalkeeper]) -> dict:
    stars = {str(n): sum(1 for p in team if p.stars == n) for n in STAR_LEVELS}
    skills: dict[str, int] = {}
    for p in team:
        if p.skill:
            skills[p.skill] = skills.get(p.skill, 0) + 1
    return {
        "players": len(team),
        "average": round(mean(p.average for p in team), 4) if team else 0.0,
        "strength": round(sum(p.average for p in team), 4),
        "stars": stars,
        "attributes": {
            k: round(mean(p.attributes.get(k, 0.0) for p in team), 4) if team else 0.0 for k in FIELD_ATTRIBUTES
        },
        "skills": skills,
        "provisional_players": sum(1 for p in team if p.provisional),
        "goalkeepers": len(goalkeepers),
        "goalkeeper_average": round(mean(g.average for g in goalkeepers), 4) if goalkeepers else None,
    }


def signature_of(teams: list[list[int]]) -> str:
    """Assinatura canônica da composição (independe da ordem dos times/jogadores)."""
    parts = sorted(",".join(str(i) for i in sorted(team)) for team in teams)
    return "|".join(parts)


# --------------------------------------------------------------------------- construção / busca


def _team_sizes(n_players: int, num_teams: int, rng: random.Random) -> list[int]:
    base, extra = divmod(n_players, num_teams)
    sizes = [base + 1] * extra + [base] * (num_teams - extra)
    rng.shuffle(sizes)
    return sizes


def _construct(players: list[DrawPlayer], sizes: list[int], rng: random.Random) -> list[list[DrawPlayer]]:
    t = len(sizes)
    teams: list[list[DrawPlayer]] = [[] for _ in range(t)]
    strength = [0.0] * t
    by_stars = {n: [p for p in players if p.stars == n] for n in STAR_LEVELS}
    for n in STAR_LEVELS:  # 5 e 4 estrelas primeiro; depois os demais níveis pela força acumulada
        group = by_stars[n][:]
        rng.shuffle(group)
        group.sort(key=lambda p: -p.average)  # estável: empates continuam aleatórios
        for p in group:
            open_teams = [i for i in range(t) if len(teams[i]) < sizes[i]]
            choice = min(
                open_teams,
                key=lambda i: (
                    sum(1 for q in teams[i] if q.stars == n),
                    strength[i] / max(1, sizes[i]),
                    rng.random(),
                ),
            )
            teams[choice].append(p)
            strength[choice] += p.average
    return teams


def _improve(
    teams: list[list[DrawPlayer]], weights: dict[str, float], rng: random.Random, deadline: float
) -> tuple[list[list[DrawPlayer]], float, bool]:
    """Hill climbing com primeira melhora. Retorna (times, custo, interrompido_por_tempo)."""
    cost, _ = evaluate(teams, weights)
    t = len(teams)
    while True:
        moves = [
            (a, i, b, j)
            for a, b in itertools.combinations(range(t), 2)
            for i in range(len(teams[a]))
            for j in range(len(teams[b]))
        ]
        rng.shuffle(moves)
        improved = False
        for a, i, b, j in moves:
            if time.monotonic() > deadline:
                return teams, cost, True
            teams[a][i], teams[b][j] = teams[b][j], teams[a][i]
            new_cost, _ = evaluate(teams, weights)
            if new_cost < cost - 1e-9:
                cost = new_cost
                improved = True
                break
            teams[a][i], teams[b][j] = teams[b][j], teams[a][i]
        if not improved:
            return teams, cost, False


def _assign_goalkeepers(
    field_teams: list[list[DrawPlayer]], goalkeepers: list[DrawGoalkeeper], per_team: int, rng: random.Random
) -> list[list[DrawGoalkeeper]]:
    t = len(field_teams)
    result: list[list[DrawGoalkeeper]] = [[] for _ in range(t)]
    if not goalkeepers:
        return result
    pool = goalkeepers[:]
    rng.shuffle(pool)
    pool.sort(key=lambda g: -g.average)
    # times de linha mais fracos recebem primeiro os melhores goleiros (serpentina)
    order = sorted(range(t), key=lambda i: (sum(p.average for p in field_teams[i]), rng.random()))
    round_no = 0
    while pool and round_no < per_team:
        seq = order if round_no % 2 == 0 else list(reversed(order))
        for i in seq:
            if not pool:
                break
            result[i].append(pool.pop(0))
        round_no += 1
    return result


# --------------------------------------------------------------------------- API pública


def run_draw(
    field_players: list[DrawPlayer], goalkeepers: list[DrawGoalkeeper], config: DrawConfig
) -> DrawResult:
    if config.num_teams < 2:
        raise DrawError("São necessárias ao menos 2 equipes")
    if config.field_per_team < 1:
        raise DrawError("Cada equipe precisa de ao menos 1 jogador de linha")
    if config.goalkeepers_per_team < 0:
        raise DrawError("Quantidade de goleiros inválida")
    ids = [p.id for p in field_players] + [g.id for g in goalkeepers]
    if len(ids) != len(set(ids)):
        raise DrawError("Jogador duplicado na lista de elegíveis")

    seed = config.seed if config.seed is not None else random.SystemRandom().randrange(2**31)
    rng = random.Random(seed)
    warnings: list[str] = []
    reserves: list[int] = []
    weights = {**DEFAULT_WEIGHTS, **normalize_weights(config.weights or {})}

    # --- Etapa 1: elegibilidade
    gk_needed = config.num_teams * config.goalkeepers_per_team
    gks_sorted = sorted(goalkeepers, key=lambda g: -g.average)
    gks = gks_sorted[:gk_needed]
    field = list(field_players)
    for extra in gks_sorted[gk_needed:]:
        if extra.field_profile is not None:
            field.append(extra.field_profile)
            warnings.append(f"Goleiro {extra.id} excedente foi incluído como jogador de linha")
        else:
            reserves.append(extra.id)
    if len(gks) < gk_needed:
        warnings.append(f"Goleiros insuficientes: {len(gks)} de {gk_needed} necessários")

    slots = config.num_teams * config.field_per_team
    if len(field) > slots:
        # a ordem de entrada representa a prioridade (ordem de confirmação)
        reserves.extend(p.id for p in field[slots:])
        field = field[:slots]
        warnings.append(f"{len(reserves)} jogador(es) ficaram como reserva por falta de vagas")
    if len(field) < config.num_teams:
        raise DrawError(
            f"Jogadores de linha insuficientes: {len(field)} para {config.num_teams} equipes"
        )
    if len(field) < slots:
        warnings.append(f"Formação incompleta: {len(field)} jogadores de linha para {slots} vagas")
    provisional = sum(1 for p in field if p.provisional)
    if provisional:
        warnings.append(f"{provisional} jogador(es) com perfil provisório (avaliações insuficientes)")

    # --- Etapas 2 a 7: construção + busca + seleção
    start = time.monotonic()
    deadline = start + max(0.05, config.time_limit_seconds)
    candidates: dict[str, tuple[float, list[list[DrawPlayer]]]] = {}
    evaluated = 0
    fallback = False
    while evaluated < config.max_candidates:
        sizes = _team_sizes(len(field), config.num_teams, rng)
        teams = _construct(field, sizes, rng)
        teams, cost, interrupted = _improve(teams, weights, rng, deadline)
        evaluated += 1
        sig = signature_of([[p.id for p in team] for team in teams])
        if sig not in candidates or candidates[sig][0] > cost:
            candidates[sig] = (cost, [team[:] for team in teams])
        if interrupted:
            fallback = evaluated == 1  # nem a primeira busca terminou: resultado é o guloso parcial
            break
        if time.monotonic() > deadline:
            break

    best_cost = min(c for c, _ in candidates.values())
    tolerance = max(config.absolute_tolerance, best_cost * config.relative_tolerance)
    pool = sorted(
        (sig for sig, (c, _) in candidates.items() if c <= best_cost + tolerance),
        key=lambda s: candidates[s][0],
    )
    fresh = [s for s in pool if s not in config.avoid_signatures]
    if config.avoid_signatures and not fresh:
        warnings.append("Não foi encontrada composição equivalente diferente dos sorteios anteriores")
    chosen_sig = rng.choice(fresh or pool)
    chosen_cost, chosen_teams = candidates[chosen_sig]
    _, metrics = evaluate(chosen_teams, weights)

    gk_teams = _assign_goalkeepers(chosen_teams, gks, config.goalkeepers_per_team, rng)
    gk_avgs = [mean(g.average for g in team) for team in gk_teams if team]
    metrics["goalkeeper_difference"] = round(_range(gk_avgs), 4) if len(gk_avgs) > 1 else 0.0
    metrics["weights"] = weights
    metrics["equivalent_candidates"] = len(pool)
    metrics["elapsed_ms"] = round((time.monotonic() - start) * 1000, 1)

    results = [
        TeamResult(
            index=i,
            field_ids=[p.id for p in chosen_teams[i]],
            goalkeeper_ids=[g.id for g in gk_teams[i]],
            stats=team_stats(chosen_teams[i], gk_teams[i]),
        )
        for i in range(config.num_teams)
    ]
    return DrawResult(
        teams=results,
        reserves=reserves,
        score=round(chosen_cost, 6),
        metrics=metrics,
        warnings=warnings,
        seed=seed,
        signature=chosen_sig,
        candidates_evaluated=evaluated,
        fallback_used=fallback,
    )
