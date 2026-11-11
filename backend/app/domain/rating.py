"""Regras puras de atributos, média, nível em estrelas e atualização de notas (PRD §5 e §7)."""

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

# Nível do jogador em estrelas (0 a 5), derivado da média dos atributos.
# (estrelas, média mínima) em ordem decrescente; com os padrões, estrelas = média arredondada.
STAR_THRESHOLDS: tuple[tuple[int, float], ...] = ((5, 4.5), (4, 3.5), (3, 2.5), (2, 1.5), (1, 0.5), (0, 0.0))
STAR_LEVELS: tuple[int, ...] = tuple(s for s, _ in STAR_THRESHOLDS)  # 5, 4, 3, 2, 1, 0
MAX_STARS = 5
STAR_LABELS = {
    5: "Craque",
    4: "Avançado",
    3: "Intermediário",
    2: "Básico",
    1: "Iniciante",
    0: "Em desenvolvimento",
}

MIN_SCORE = 0.0
MAX_SCORE = 5.0
# precisão usada para comparar com os limites das estrelas, evitando erro de ponto flutuante (ex.: 4.4999999)
_STAR_PRECISION = 6


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


Thresholds = tuple[tuple[int, float], ...]


def thresholds_from_dict(data: dict) -> Thresholds:
    """{"5": 4.5, "4": 3.5, ...} → limites em ordem decrescente (0 estrela sempre começa em 0).

    As chaves podem vir como texto (JSON) ou inteiro."""
    clean = {int(k): float(v) for k, v in data.items()}
    return tuple((s, clean[s]) for s in STAR_LEVELS[:-1]) + ((0, 0.0),)


def thresholds_to_dict(thresholds: Thresholds) -> dict[str, float]:
    return {str(s): m for s, m in thresholds if s != 0}


def validate_thresholds(data: dict) -> dict[str, float]:
    try:
        clean = {int(k): float(v) for k, v in data.items()}
    except (TypeError, ValueError):
        raise ValueError("Informe a média mínima de 1 a 5 estrelas") from None
    expected = set(STAR_LEVELS[:-1])
    if set(clean) != expected:
        raise ValueError("Informe a média mínima de 1 a 5 estrelas")
    values = [clean[s] for s in STAR_LEVELS[:-1]]  # 5, 4, 3, 2, 1
    if not all(MIN_SCORE < v <= MAX_SCORE for v in values):
        raise ValueError("As médias mínimas devem estar entre 0 e 5")
    if any(a <= b for a, b in zip(values, values[1:], strict=False)):
        raise ValueError(
            "Cada nível precisa de média mínima maior que a do nível abaixo (5★ > 4★ > 3★ > 2★ > 1★)"
        )
    return {str(s): clean[s] for s in STAR_LEVELS[:-1]}


def compute_stars(average: float, thresholds: Thresholds | None = None) -> int:
    value = round(average, _STAR_PRECISION)
    for stars, minimum in thresholds or STAR_THRESHOLDS:
        if value >= minimum:
            return stars
    return 0


def target_average(stars: int, thresholds: Thresholds | None = None) -> float:
    """Média no meio da faixa de um nível (com os limites padrão, 4★ = 4,0; 5★ = 4,75; 0★ = 0,25)."""
    bounds = dict(thresholds or STAR_THRESHOLDS)
    low = bounds[stars]
    high = bounds[stars + 1] if stars < MAX_STARS else MAX_SCORE
    return (low + high) / 2


def attributes_for_stars(
    attributes: dict[str, float], keys: tuple[str, ...], stars: int, thresholds: Thresholds | None = None
) -> dict[str, float]:
    """Leva os atributos ao nível `stars` preservando o perfil (pontos fortes e fracos).

    Se o nível já é esse, nada muda. Senão, desloca todos os atributos pela mesma diferença até a média ficar no
    meio da faixa do nível; se os limites 0 e 5 cortarem demais o deslocamento, usa a média-alvo em todos."""
    if compute_stars(compute_average(attributes, keys), thresholds) == stars:
        return {k: float(attributes[k]) for k in keys}
    target = target_average(stars, thresholds)
    delta = target - compute_average(attributes, keys)
    shifted = {k: min(MAX_SCORE, max(MIN_SCORE, float(attributes[k]) + delta)) for k in keys}
    if compute_stars(compute_average(shifted, keys), thresholds) == stars:
        return shifted
    return initial_attributes(keys, target)


def star_label(stars: int) -> str:
    return STAR_LABELS.get(int(stars), "")


# Sorteios antigos guardaram o Tier (letra) dos jogadores; equivalência aproximada em estrelas
LEGACY_TIER_STARS = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}


def stars_of(value) -> int:
    """Nível em estrelas de um valor salvo (inteiro novo ou letra de Tier dos registros antigos)."""
    if isinstance(value, str) and value in LEGACY_TIER_STARS:
        return LEGACY_TIER_STARS[value]
    try:
        return max(0, min(MAX_STARS, int(value)))
    except (TypeError, ValueError):
        return 0


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
