"""O catálogo de conquistas.

Uma conquista é uma condição sobre :class:`AchievementContext` mais o texto que
a explica. Nada disso é dado: é regra, e regra mora no código, versionada junto
com o resto (ver ``app.models.achievement`` para o porquê de o banco guardar só
a chave e a data).

O contexto é um objeto tipado, não um dicionário. Uma condição escrita como
``ctx.max_per_day >= 5`` quebra o carregamento do módulo no dia em que o campo
mudar de nome; ``ctx["max_per_day"]`` quebraria em produção, no meio de uma
conclusão de meta, com um ``KeyError``.

As faixas são geradas em série porque é isso que elas são - a mesma condição
com um número diferente. Escrever nove vezes o mesmo lambda seria nove
oportunidades de errar um deles.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.goal import CATEGORY_ICONS, CATEGORY_LABELS, GOAL_CATEGORIES


@dataclass(slots=True, frozen=True)
class AchievementContext:
    """Tudo que uma condição pode perguntar sobre a jornada."""

    created: int = 0
    completed: int = 0
    level: int = 0
    streak: int = 0
    record: int = 0
    productive_days: int = 0

    categories_completed: frozenset[str] = frozenset()
    max_category_count: int = 0

    priority_alta: int = 0
    priority_media: int = 0
    priority_baixa: int = 0
    priorities_completed: int = 0

    early_bird: bool = False
    night_owl: bool = False
    weekend_completed: bool = False
    undated_completed: bool = False
    max_per_day: int = 0
    completion_rate: float = 0.0

    journey_days: int = 0
    light_theme: bool = False
    phrases_enabled: bool = False
    has_template: bool = False
    linked_to_document: bool = False


@dataclass(slots=True, frozen=True)
class Achievement:
    key: str
    title: str
    description: str
    icon: str
    group: str
    condition: Callable[[AchievementContext], bool] = field(compare=False)


def _tiers(
    prefix: str,
    group: str,
    icon: str,
    reader: Callable[[AchievementContext], int],
    description: str,
    tiers: tuple[tuple[int, str], ...],
) -> list[Achievement]:
    """Uma família de conquistas que só difere no número exigido.

    ``threshold`` entra no ``lambda`` como argumento com valor padrão, e não
    por captura: capturado, ele seria o último valor do laço em todas as
    condições - o erro clássico de fechar sobre uma variável de iteração.
    """
    return [
        Achievement(
            key=f"{prefix}_{threshold}",
            title=title,
            description=description.format(n=threshold),
            icon=icon,
            group=group,
            condition=(lambda ctx, limit=threshold: reader(ctx) >= limit),
        )
        for threshold, title in tiers
    ]


CATALOG: list[Achievement] = []

CATALOG += _tiers(
    "created", "Lançamentos", "rocket", lambda ctx: ctx.created, "Crie {n} metas",
    (
        (1, "Primeiro lançamento"), (5, "Plataforma de lançamento"), (10, "Em órbita"),
        (25, "Velocidade de escape"), (50, "Rumo à Lua"), (100, "Cinturão de asteroides"),
        (200, "Vizinhança de Marte"), (500, "Sistema solar interior"),
        (1000, "Espaço interestelar"),
    ),
)

CATALOG += _tiers(
    "completed", "Conclusões", "check-circle", lambda ctx: ctx.completed,
    "Conclua {n} metas",
    (
        (1, "Primeira conquista"), (5, "Propulsão"), (10, "Combustão total"),
        (25, "Segundo estágio"), (50, "Órbita estável"), (100, "Centena estelar"),
        (200, "Constelação própria"), (500, "Galáxia pessoal"), (1000, "Lenda cósmica"),
    ),
)

CATALOG += _tiers(
    "streak", "Sequência", "flame", lambda ctx: ctx.streak,
    "Mantenha uma sequência de {n} dias",
    (
        (3, "Ignição"), (5, "Combustível estável"), (7, "Semana estelar"),
        (14, "Quinzena orbital"), (21, "Hábito em órbita"), (30, "Mês sem gravidade"),
        (60, "Dois meses no espaço"), (90, "Trimestre estelar"),
        (180, "Meio ano além da atmosfera"), (365, "Um ano ao redor do sol"),
    ),
)

CATALOG += _tiers(
    "record", "Recorde", "trophy", lambda ctx: ctx.record,
    "Alcance um recorde de {n} dias seguidos",
    (
        (3, "Marca pessoal"), (7, "Recorde de uma semana"), (14, "Recorde de duas semanas"),
        (30, "Recorde de um mês"), (60, "Recorde de dois meses"),
        (90, "Recorde de um trimestre"), (180, "Recorde de meio ano"),
        (365, "Recorde de um ano"), (730, "Recorde de dois anos"),
    ),
)

CATALOG += _tiers(
    "level", "Nível", "award", lambda ctx: ctx.level, "Alcance o nível {n}",
    (
        (2, "Decolagem"), (3, "Subindo de órbita"), (5, "Piloto júnior"), (10, "Piloto"),
        (15, "Comandante de missão"), (20, "Capitão estelar"), (25, "Almirante da frota"),
        (30, "Explorador veterano"), (40, "Navegador cósmico"), (50, "Mestre do cosmos"),
        (75, "Lenda da frota"), (100, "Imperador das estrelas"),
    ),
)

CATALOG += _tiers(
    "focus_days", "Disciplina", "calendar-check", lambda ctx: ctx.productive_days,
    "Tenha {n} dias produtivos",
    (
        (1, "Primeiro dia produtivo"), (7, "Semana de foco"), (14, "Quinzena de foco"),
        (30, "Mês de disciplina"), (60, "Dois meses de disciplina"),
        (100, "Cem dias produtivos"), (200, "Duzentos dias produtivos"),
        (365, "Um ano de disciplina"),
    ),
)

CATALOG += _tiers(
    "priority_alta", "Prioridade", "target", lambda ctx: ctx.priority_alta,
    "Conclua {n} metas de prioridade alta",
    (
        (1, "Primeira prioridade"), (5, "Foco no essencial"), (10, "Dez alvos críticos"),
        (25, "Mestre das prioridades"), (50, "Disciplina de elite"),
        (100, "Cem missões críticas"),
    ),
)

CATALOG += _tiers(
    "priority_media", "Prioridade", "layers", lambda ctx: ctx.priority_media,
    "Conclua {n} metas de prioridade média",
    ((1, "Equilíbrio inicial"), (10, "Rotina estável"), (50, "Constância média")),
)

CATALOG += _tiers(
    "priority_baixa", "Prioridade", "moon", lambda ctx: ctx.priority_baixa,
    "Conclua {n} metas de prioridade baixa",
    ((1, "Sem pressa"), (10, "Passo a passo"), (50, "Leveza constante")),
)

CATALOG += _tiers(
    "journey", "Jornada", "clock", lambda ctx: ctx.journey_days,
    "Mantenha sua jornada viva por {n} dias",
    (
        (30, "Um mês de jornada"), (90, "Tripulação experiente"),
        (365, "Um ano de missão"), (730, "Dois anos de missão"),
    ),
)

CATEGORY_ACHIEVEMENT_TITLES = {
    "estudos": "Mente em expansão",
    "trabalho": "Missão profissional",
    "saude": "Corpo em órbita",
    "financas": "Cofre estelar",
    "espiritual": "Cuidado interior",
    "pessoal": "Jornada interior",
    "familia": "Tripulação de casa",
    "empreendedorismo": "Fundador de mundos",
    "outros": "Território inexplorado",
}

CATALOG += [
    Achievement(
        key=f"category_{category}",
        title=CATEGORY_ACHIEVEMENT_TITLES[category],
        description=f"Conclua uma meta em {CATEGORY_LABELS[category]}",
        icon=CATEGORY_ICONS[category],
        group="Categorias",
        condition=(lambda ctx, name=category: name in ctx.categories_completed),
    )
    for category in GOAL_CATEGORIES
]

CATALOG += [
    Achievement(
        key="all_categories",
        title="Explorador completo",
        description="Conclua ao menos uma meta em todas as categorias",
        icon="globe",
        group="Categorias",
        condition=lambda ctx: len(ctx.categories_completed) >= len(GOAL_CATEGORIES),
    ),
    Achievement(
        key="category_master_10",
        title="Especialista de categoria",
        description="Conclua 10 metas em uma mesma categoria",
        icon="check-circle",
        group="Categorias",
        condition=lambda ctx: ctx.max_category_count >= 10,
    ),
    Achievement(
        key="category_master_25",
        title="Autoridade na área",
        description="Conclua 25 metas em uma mesma categoria",
        icon="check-circle",
        group="Categorias",
        condition=lambda ctx: ctx.max_category_count >= 25,
    ),
    Achievement(
        key="all_priorities",
        title="Visão 360",
        description="Conclua metas das três prioridades",
        icon="layers",
        group="Prioridade",
        condition=lambda ctx: ctx.priorities_completed >= 3,
    ),
    Achievement(
        key="early_bird",
        title="Madrugador",
        description="Conclua uma meta antes das 7h",
        icon="sunrise",
        group="Ritmo",
        condition=lambda ctx: ctx.early_bird,
    ),
    Achievement(
        key="night_owl",
        title="Coruja noturna",
        description="Conclua uma meta depois das 22h",
        icon="moon",
        group="Ritmo",
        condition=lambda ctx: ctx.night_owl,
    ),
    Achievement(
        key="weekend_warrior",
        title="Guerreiro de fim de semana",
        description="Conclua uma meta em um sábado ou domingo",
        icon="sun",
        group="Ritmo",
        condition=lambda ctx: ctx.weekend_completed,
    ),
    Achievement(
        key="undated_done",
        title="Sem prazo, sem pressa",
        description="Conclua uma meta que não tinha data marcada",
        icon="infinity",
        group="Estilo de missão",
        condition=lambda ctx: ctx.undated_completed,
    ),
    Achievement(
        key="daily_5",
        title="Maratonista",
        description="Conclua 5 metas em um único dia",
        icon="bolt",
        group="Estilo de missão",
        condition=lambda ctx: ctx.max_per_day >= 5,
    ),
    Achievement(
        key="daily_10",
        title="Ultramaratonista",
        description="Conclua 10 metas em um único dia",
        icon="bolt",
        group="Estilo de missão",
        condition=lambda ctx: ctx.max_per_day >= 10,
    ),
    Achievement(
        key="rate_50",
        title="Equilíbrio orbital",
        description="Conclua 50% das metas criadas (mínimo de 10)",
        icon="pie",
        group="Consistência",
        condition=lambda ctx: ctx.created >= 10 and ctx.completion_rate >= 0.5,
    ),
    Achievement(
        key="rate_75",
        title="Trajetória precisa",
        description="Conclua 75% das metas criadas (mínimo de 10)",
        icon="pie",
        group="Consistência",
        condition=lambda ctx: ctx.created >= 10 and ctx.completion_rate >= 0.75,
    ),
    Achievement(
        key="rate_100",
        title="Acerto perfeito",
        description="Conclua 100% das metas criadas (mínimo de 10)",
        icon="target",
        group="Consistência",
        condition=lambda ctx: ctx.created >= 10 and ctx.completion_rate >= 1,
    ),
    # ── Comando: as conquistas que são desta aplicação, e de nenhuma outra ──
    Achievement(
        key="linked_document",
        title="Missão com endereço",
        description="Ligue uma meta a um documento da sua biblioteca",
        icon="link",
        group="Comando",
        condition=lambda ctx: ctx.linked_to_document,
    ),
    Achievement(
        key="template_created",
        title="Catálogo pronto",
        description="Guarde uma meta predefinida para ativar quando quiser",
        icon="bookmark",
        group="Comando",
        condition=lambda ctx: ctx.has_template,
    ),
    Achievement(
        key="light_theme",
        title="Painel claro",
        description="Experimente a aplicação no tema claro",
        icon="sun",
        group="Comando",
        condition=lambda ctx: ctx.light_theme,
    ),
    Achievement(
        key="phrases_on",
        title="Em boa companhia",
        description="Deixe as frases motivacionais ligadas",
        icon="quote",
        group="Comando",
        condition=lambda ctx: ctx.phrases_enabled,
    ),
]

BY_KEY: dict[str, Achievement] = {item.key: item for item in CATALOG}

# A ordem dos grupos na tela é a ordem em que eles aparecem no catálogo, e não
# a ordem alfabética: as faixas vão do começo da jornada para o fim dela.
GROUP_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(item.group for item in CATALOG)
)
