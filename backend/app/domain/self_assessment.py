"""Quiz de autoavaliação ao entrar em um grupo.

Cinco perguntas simples, uma para cada atributo de linha avaliado após a pelada (finalização, passe, drible,
defesa e físico). Goleiros (grupo com avaliação própria de goleiro) respondem cinco perguntas sobre reflexo,
posicionamento, saída do gol, reposição e um contra um. Cada resposta vira a nota inicial provisória daquele
atributo; as avaliações dos colegas corrigem o nível com o tempo (perfil provisório converge mais rápido).

A autoavaliação é conservadora: a resposta mais alta vale 4,5, nunca 5. Quem se diz craque começa no topo,
mas a nota máxima só vem com a confirmação dos colegas.

Componente de domínio puro: não conhece banco de dados nem HTTP.
"""

from app.domain.rating import FIELD_ATTRIBUTES, GOALKEEPER_ATTRIBUTES

# nota atribuída a cada opção, da mais fraca (0) à mais forte (4)
OPTION_SCORES: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 4.5)

QUESTIONS: tuple[dict, ...] = (
    {
        "attribute": "passing",
        "question": "Você acha que consegue tocar bem a bola?",
        "hint": "Passe curto e longo chegando no pé do companheiro.",
        "options": (
            "Erro bastante passe",
            "Acerto os passes curtos",
            "Toco bem na maioria das vezes",
            "Toco bem e acho o companheiro livre",
            "Passe é meu forte, curto ou longo",
        ),
    },
    {
        "attribute": "dribbling",
        "question": "Você sabe driblar e construir jogadas?",
        "hint": "Sair da marcação, carregar a bola e criar a jogada.",
        "options": (
            "Prefiro soltar a bola rápido",
            "Driblo de vez em quando",
            "Consigo sair da marcação",
            "Driblo bem e crio jogadas",
            "Parto pra cima e decido o jogo",
        ),
    },
    {
        "attribute": "finishing",
        "question": "Como você é na hora de chutar ao gol?",
        "hint": "Finalização, cabeceio e aproveitamento das chances.",
        "options": (
            "Quase não finalizo",
            "Faço um gol de vez em quando",
            "Aproveito algumas chances",
            "Faço gol com frequência",
            "Sou artilheiro, não perco chance",
        ),
    },
    {
        "attribute": "defense",
        "question": "Você sabe marcar e roubar a bola do adversário?",
        "hint": "Desarme, posicionamento e ajuda na defesa.",
        "options": (
            "Marcar não é comigo",
            "Ajudo quando dá",
            "Marco bem na maioria das vezes",
            "Desarmo bem e me posiciono certo",
            "Sou uma muralha lá atrás",
        ),
    },
    {
        "attribute": "physical",
        "question": "Como está seu fôlego para a pelada?",
        "hint": "Resistência, velocidade e força na disputa de bola.",
        "options": (
            "Canso logo no começo",
            "Aguento um pedaço do jogo",
            "Aguento bem o jogo",
            "Corro o jogo inteiro",
            "Tenho gás de sobra e ganho as divididas",
        ),
    },
)

# goleiros (modo de avaliação própria do grupo): cinco perguntas sobre os atributos de goleiro
GOALKEEPER_QUESTIONS: tuple[dict, ...] = (
    {
        "attribute": "reflexes",
        "question": "Como estão seus reflexos debaixo da trave?",
        "hint": "Defesas em chutes de perto e bolas rápidas.",
        "options": (
            "A bola passa antes de eu reagir",
            "Pego as mais fáceis",
            "Faço boas defesas às vezes",
            "Faço defesas difíceis com frequência",
            "Tenho reflexo de paredão",
        ),
    },
    {
        "attribute": "positioning",
        "question": "Você se posiciona bem no gol?",
        "hint": "Fechar o ângulo e estar no lugar certo na hora do chute.",
        "options": (
            "Fico perdido no gol",
            "Às vezes fico mal posicionado",
            "Me posiciono bem na maioria das vezes",
            "Fecho bem o ângulo",
            "Estou sempre no lugar certo",
        ),
    },
    {
        "attribute": "rushing_out",
        "question": "Como você é saindo do gol?",
        "hint": "Sair nos cruzamentos, nas bolas longas e no pé do atacante.",
        "options": (
            "Prefiro não sair do gol",
            "Saio só quando é fácil",
            "Saio bem na maioria das vezes",
            "Saio com segurança e tempo certo",
            "Domino a área toda",
        ),
    },
    {
        "attribute": "distribution",
        "question": "Como é sua reposição de bola?",
        "hint": "Tiro de meta, lançamento com a mão e passe com o pé.",
        "options": (
            "Erro bastante a reposição",
            "Acerto as reposições curtas",
            "Reponho bem na maioria das vezes",
            "Começo boas jogadas na reposição",
            "Minha reposição vira ataque",
        ),
    },
    {
        "attribute": "one_on_one",
        "question": "Como você vai no mano a mano com o atacante?",
        "hint": "Quando o atacante sai cara a cara com você.",
        "options": (
            "Quase sempre levo o gol",
            "Defendo de vez em quando",
            "Ganho uma boa parte",
            "Ganho a maioria",
            "Atacante treme na minha frente",
        ),
    },
)

assert tuple(sorted(q["attribute"] for q in QUESTIONS)) == tuple(sorted(FIELD_ATTRIBUTES))
assert tuple(sorted(q["attribute"] for q in GOALKEEPER_QUESTIONS)) == tuple(sorted(GOALKEEPER_ATTRIBUTES))
assert all(len(q["options"]) == len(OPTION_SCORES) for q in QUESTIONS + GOALKEEPER_QUESTIONS)


def questions_for(kind: str = "field") -> tuple[dict, ...]:
    """Perguntas do quiz de linha (`field`) ou de goleiro (`goalkeeper`)."""
    return GOALKEEPER_QUESTIONS if kind == "goalkeeper" else QUESTIONS


def validate_answers(answers: dict[str, int], kind: str = "field") -> dict[str, int]:
    """Uma resposta (índice da opção) para cada uma das cinco perguntas."""
    expected = {q["attribute"] for q in questions_for(kind)}
    if set(answers) != expected:
        missing = sorted(expected - set(answers))
        extra = sorted(set(answers) - expected)
        raise ValueError(f"Responda todas as perguntas. Faltando: {missing}; inesperadas: {extra}")
    clean: dict[str, int] = {}
    for key, value in answers.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(OPTION_SCORES):
            raise ValueError(f"Resposta inválida para '{key}'")
        clean[key] = value
    return clean


def scores_from_answers(answers: dict[str, int], kind: str = "field") -> dict[str, float]:
    """Respostas → notas dos atributos (0 a 5)."""
    clean = validate_answers(answers, kind)
    return {q["attribute"]: OPTION_SCORES[clean[q["attribute"]]] for q in questions_for(kind)}
