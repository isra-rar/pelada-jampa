"""Regras puras de atributos, média, Tier e atualização de notas (PRD §5 e §7)."""

from dataclasses import dataclass
from statistics import median

from app.domain.enums import GoalkeeperRatingMode, RatingKind

FIELD_ATTRIBUTES: tuple[str, ...] = ("finishing", "passing", "dribbling", "defense", "physical")
GOALKEEPER_ATTRIBUTES: tuple[str, ...] = ("reflexes", "positioning", "rushing_out", "distribution", "one_on_one")

ATTRIBUTE_LABELS = {
    "finishing": "Finalização",
    "passing": "Passe",
    "dribbling": "Drible",
    "defense": "Defesa",
    "physical": "Físico",
    "reflexes": "Reflexo",
    "positioning": "Posicionamento",
    "rushing_out": "Saída do gol",
    "distribution": "Reposição",
    "one_on_one": "Um contra um",
}

# (tier, média mínima) em ordem decrescente
TIER_THRESHOLDS: tuple[tuple[str, float], ...] = (("S", 4.5), ("A", 3.75), ("B", 3.0), ("C", 2.0), ("D", 0.0))
TIER_ORDER: tuple[str, ...] = tuple(t for t, _ in TIER_THRESHOLDS)
TIER_VALUES = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
TIER_LABELS = {"S": "Elite", "A": "Avançado", "B": "Intermediário", "C": "Iniciante", "D": "Desenvolvimento"}

MIN_SCORE = 0.0
MAX_SCORE = 5.0
# precisão usada para comparar com os limites de Tier, evitando erro de ponto flutuante (ex.: 4.4999999)
_TIER_PRECISION = 6


def attributes_for(
    kind: RatingKind | str, goalkeeper_mode: GoalkeeperRatingMode | str = GoalkeeperRatingMode.OWN
) -> tuple[str, ...]:
    """Conjunto de atributos de uma classificação.

    Goleiros têm classificação separada; no modo `general` ela usa os mesmos cinco atributos de linha.
    """
    is_goalkeeper = RatingKind(kind) == RatingKind.GOALKEEPER
    if is_goalkeeper and GoalkeeperRatingMode(goalkeeper_mode) == GoalkeeperRatingMode.OWN:
        return GOALKEEPER_ATTRIBUTES
    return FIELD_ATTRIBUTES


def validate_scores(scores: dict[str, float], keys: tuple[str, ...]) -> dict[str, float]:
    expected = set(keys)
    got = set(scores)
    if got != expected:
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        raise ValueError(f"Atributos inválidos. Faltando: {missing}; inesperados: {extra}")
    clean: dict[str, float] = {}
    for key in keys:
        value = float(scores[key])
        if not MIN_SCORE <= value <= MAX_SCORE:
            raise ValueError(f"'{key}' deve estar entre 0 e 5")
        clean[key] = value
    return clean


def compute_average(attributes: dict[str, float], keys: tuple[str, ...] | None = None) -> float:
    keys = keys or tuple(attributes)
    return sum(float(attributes[k]) for k in keys) / len(keys)


def compute_tier(average: float) -> str:
    value = round(average, _TIER_PRECISION)
    for tier, minimum in TIER_THRESHOLDS:
        if value >= minimum:
            return tier
    return "D"


def display_average(average: float) -> float:
    return round(average + 1e-9, 1)


def detect_outliers(score_sets: list[dict[str, float]], keys: tuple[str, ...], threshold: float) -> list[bool]:
    """Marca avaliações cujo valor em algum atributo difere da mediana das demais mais que `threshold`.

    Exige ao menos 3 avaliações para o alvo; abaixo disso não há base estatística e nada é marcado.
    """
    n = len(score_sets)
    flags = [False] * n
    if n < 3:
        return flags
    for i, scores in enumerate(score_sets):
        others = [s for j, s in enumerate(score_sets) if j != i]
        for key in keys:
            ref = median(o[key] for o in others)
            if abs(scores[key] - ref) > threshold:
                flags[i] = True
                break
    return flags


def peer_median(score_sets: list[dict[str, float]], keys: tuple[str, ...]) -> dict[str, float]:
    return {k: float(median(s[k] for s in score_sets)) for k in keys} if score_sets else {}


def consolidate(score_sets: list[dict[str, float]], keys: tuple[str, ...]) -> dict[str, float]:
    return {k: sum(s[k] for s in score_sets) / len(score_sets) for k in keys}


@dataclass(frozen=True)
class RatingParams:
    match_weight: float = 0.30  # nota_nova = anterior*(1-w) + partida*w  (0,70 / 0,30 no PRD)
    provisional_match_weight: float = 0.50  # perfis provisórios convergem mais rápido
    min_evaluators_full_weight: int = 3  # abaixo disso o peso é reduzido proporcionalmente


def effective_weight(n_evaluations: int, provisional: bool, params: RatingParams) -> float:
    if n_evaluations <= 0:
        return 0.0
    base = params.provisional_match_weight if provisional else params.match_weight
    reliability = min(1.0, n_evaluations / max(1, params.min_evaluators_full_weight))
    return base * reliability


def update_attributes(
    current: dict[str, float],
    match_scores: dict[str, float],
    n_evaluations: int,
    provisional: bool,
    keys: tuple[str, ...],
    params: RatingParams,
) -> tuple[dict[str, float], float]:
    weight = effective_weight(n_evaluations, provisional, params)
    if weight == 0.0:
        return {k: float(current[k]) for k in keys}, 0.0
    new = {k: float(current[k]) * (1 - weight) + float(match_scores[k]) * weight for k in keys}
    return new, weight


def initial_attributes(keys: tuple[str, ...], value: float) -> dict[str, float]:
    return {k: float(value) for k in keys}
