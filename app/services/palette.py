"""As paletas do sistema: o papel e a tinta, nomeados e trocáveis.

Até aqui a aplicação tinha uma paleta só. Ela morava em ``base.css``, escrita
à mão nos dois blocos de tokens, e a única coisa que o usuário podia trocar
era a cor de destaque — uma cor sobre um fundo que ele não escolhia.

Este módulo inverte isso. Uma paleta passa a ser um objeto: dois conjuntos de
superfícies (um para cada tema) e a cor de destaque que nasce com ela. O CSS
gerado em ``/assets/theme.css`` emite o conjunto inteiro, então trocar de
paleta troca o fundo, o papel dos painéis, a tinta do texto e a régua das
bordas de uma vez — não só o botão azul.

Três decisões que valem explicar:

**As superfícies vivem aqui, não no CSS.** ``base.css`` continua declarando a
paleta padrão, mas como reserva: é o que se vê no instante antes de a folha
gerada chegar, e o que se veria se ela nunca chegasse. A fonte da verdade é
esta tabela, e existe um teste que falha se as duas divergirem.

**As cores semânticas não pertencem à paleta.** Verde de concluído, âmbar de
atenção, vermelho de perigo: são estados, não estilo. Uma paleta que
recolorisse o "erro" para combinar com a marca estaria trocando significado
por decoração, e a mesma regra vale para as duas séries dos gráficos — uma
série que muda de cor quando alguém troca de tema é uma série sem identidade.
Por isso os dois conjuntos ficam em ``base.css`` e em ``charts.css``, válidos
para qualquer paleta. Ambos foram verificados contra as superfícies de todas
as paletas daqui.

**Nenhuma paleta entra sem passar no AA.** Os pares de contraste de cada uma
são medidos na suíte, nos dois temas, com os mesmos limites que a paleta
original teve de cumprir. Acrescentar uma paleta é acrescentar uma entrada
neste arquivo; se ela não se lê, o teste diz qual par e por quanto.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Surfaces:
    """Um tema de uma paleta: do fundo da página até a borda mais forte.

    A ordem dos campos é a ordem em que eles se empilham na tela — ``sunken``
    abaixo de ``bg``, ``bg`` abaixo de ``surface`` — e é o que permite a uma
    barra lateral parecer rebaixada e a um painel parecer levantado sem que
    nenhum dos dois precise de sombra.
    """

    bg: str
    surface: str
    surface_2: str
    surface_sunken: str

    text: str
    text_muted: str
    text_subtle: str

    border: str
    border_strong: str

    def as_css(self) -> dict[str, str]:
        """Os mesmos valores com os nomes que o CSS usa."""
        return {
            "bg": self.bg,
            "surface": self.surface,
            "surface-2": self.surface_2,
            "surface-sunken": self.surface_sunken,
            "text": self.text,
            "text-muted": self.text_muted,
            "text-subtle": self.text_subtle,
            "border": self.border,
            "border-strong": self.border_strong,
        }


@dataclass(frozen=True)
class Palette:
    """Uma paleta nomeada, nos dois temas, com a cor com que ela nasce."""

    key: str
    name: str
    description: str

    #: A cor de destaque que acompanha a paleta. Ela não é imposta: o campo
    #: "Cor principal" continua sendo do usuário, e a tela de configurações
    #: apenas sugere esta quando ele troca de paleta.
    accent: str

    light: Surfaces
    dark: Surfaces

    @property
    def swatch(self) -> tuple[str, str, str]:
        """As três cores que identificam a paleta na hora de escolher.

        O fundo escuro, o papel claro e o destaque — nesta ordem, porque é a
        leitura mais rápida possível de "que aplicação eu vou ter".
        """
        return (self.dark.bg, self.light.bg, self.accent)


PAPEL = Palette(
    key="papel",
    name="Papel",
    description="Tinta quente sobre papel quente, com um verde-petróleo de destaque.",
    # Split-complementar, e de propósito: o complemento do papel (matiz 88)
    # seria o azul-violeta em 268, e este verde-petróleo fica a 84° de cada um
    # dos dois lados. É a relação clássica da tinta ferro-gálica sobre papel
    # de trapo — a única cor fria numa tela inteiramente quente.
    accent="#0F6E64",
    light=Surfaces(
        bg="#F5F2EC",
        surface="#FDFCFA",
        surface_2="#EFEBE3",
        surface_sunken="#E7E2D8",
        text="#1A1712",
        text_muted="#5C5548",
        text_subtle="#686153",
        border="#E0DACE",
        border_strong="#C7BFB0",
    ),
    dark=Surfaces(
        # Um matiz só, 88, do preto da página à borda mais forte — e o croma
        # subindo a cada degrau que se afasta do preto (1,3 / 2,3 / 3,8 / 6,8).
        # Antes a página estava em 54 e o painel levantado em 90: 31° de
        # deriva dentro da mesma paleta, o que fazia o fundo puxar para o
        # laranja enquanto o painel puxava para o amarelo.
        bg="#13110D",
        surface="#1B1813",
        surface_2="#252118",
        surface_sunken="#0C0A07",
        text="#F2EEE7",
        text_muted="#A39C90",
        text_subtle="#948D80",
        border="#363025",
        border_strong="#4C4537",
    ),
)


CARVAO = Palette(
    key="carvao",
    name="Carvão",
    description="Preto neutro e ouro velho — os cinzas perdem o marrom e o destaque ganha metal.",
    accent="#94771E",
    light=Surfaces(
        # Osso, não papel: o mesmo gesto do tema Papel com quase toda a
        # temperatura retirada, para que o ouro seja a única coisa quente na
        # tela em vez de mais uma cor morna entre outras.
        bg="#F4F3F0",
        surface="#FCFBF9",
        surface_2="#EBE9E4",
        surface_sunken="#E2E0DA",
        text="#121110",
        text_muted="#57544E",
        text_subtle="#66625B",
        border="#DEDBD4",
        border_strong="#C3BFB6",
    ),
    dark=Surfaces(
        # Preto de verdade no fundo da página, e um cinza com um resto de
        # calor nos painéis: ouro sobre um cinza perfeitamente neutro fica
        # esverdeado, e uma pitada de vermelho no papel resolve isso sem que
        # o preto deixe de ser preto.
        bg="#0A0A0A",
        surface="#141312",
        surface_2="#1E1C1A",
        surface_sunken="#050505",
        text="#EDEBE8",
        text_muted="#A19D97",
        text_subtle="#8B8781",
        border="#302E2B",
        border_strong="#46433E",
    ),
)


DARK = Palette(
    key="dark",
    name="Dark",
    description="Azul-meia-noite e um azul elétrico — a paleta fria, feita para o escuro.",
    accent="#3B82F6",
    light=Surfaces(
        # O claro desta paleta não é papel: é vidro. Nenhum resto de amarelo
        # em nenhuma superfície, e um azul mínimo em todas — é o que faz o
        # tema claro daqui parecer o mesmo produto que o escuro, em vez de
        # um tema claro qualquer com um botão azul.
        bg="#ECEFF4",
        surface="#F8FAFC",
        surface_2="#E1E6EE",
        surface_sunken="#D6DCE6",
        text="#0D111A",
        text_muted="#4F5661",
        text_subtle="#575F6B",
        border="#D5DAE3",
        border_strong="#B7BECB",
    ),
    dark=Surfaces(
        # A razão de existir desta paleta. Carvão é preto neutro; aqui o preto
        # tem azul dentro, e os painéis abrem o azul um pouco mais a cada
        # degrau — de #090D14 na página a #18202E no painel levantado. É a
        # diferença entre uma tela apagada e uma tela escura de propósito.
        bg="#090D14",
        surface="#101623",
        surface_2="#18202E",
        surface_sunken="#05080D",
        text="#E7ECF4",
        text_muted="#98A1B0",
        text_subtle="#8791A2",
        border="#242D3C",
        border_strong="#374255",
    ),
)


VERDE = Palette(
    key="verde",
    name="Verde",
    description="Mata fechada e um verde-esmeralda — a única paleta com matiz próprio nas duas pontas.",
    # Análoga, e a 8° das superfícies: o esmeralda em 158, o papel e a tinta
    # em 150. É a mesma relação que sustenta a paleta Dark (accent em 285,
    # superfícies em 270) — perto o bastante para o destaque parecer nascido
    # do fundo, longe o bastante para não sumir nele.
    accent="#10A46A",
    light=Surfaces(
        # Verde no papel, não só no botão. Pouco: o suficiente para a página
        # não ser cinza, pouco o bastante para o texto preto continuar sendo
        # texto preto e não uma escolha de cor.
        bg="#EAF3EC",
        surface="#F6FBF7",
        surface_2="#DDEAE0",
        surface_sunken="#D2E3D5",
        text="#0F1611",
        text_muted="#49574C",
        text_subtle="#516155",
        border="#D0E1D3",
        border_strong="#B1C6B5",
    ),
    dark=Surfaces(
        # O escuro é onde esta paleta se decide: um preto que puxa para o
        # verde-garrafa em vez de para o cinza. O esmeralda do destaque cai
        # sobre ele como a mesma cor mais acesa, e não como um enxerto.
        #
        # Matiz 150 aqui também. Os dois temas eram verdes diferentes — 141 no
        # claro e 166 no escuro —, e uma paleta cujo tema escuro é 25° mais
        # azul que o claro não é uma paleta, são duas.
        bg="#0C140E",
        surface="#121D14",
        surface_2="#192A1D",
        surface_sunken="#060B07",
        text="#E8F1EA",
        text_muted="#93A596",
        text_subtle="#839787",
        border="#223527",
        border_strong="#354B3A",
    ),
)


ARDOSIA = Palette(
    key="ardosia",
    name="Ardósia",
    description="Ardósia e índigo sobre cartões brancos — a aparência original da aplicação, restaurada.",
    # A cor exata do commit 82f5fdb, sem uma casa de diferença. É o que torna
    # esta paleta reconhecível: quem trabalhou na aplicação antiga reconhece o
    # índigo antes de ler o nome.
    accent="#4F46E5",
    light=Surfaces(
        # Cartões de branco puro sobre uma página fria — nenhuma outra paleta
        # daqui faz isso, e é metade da memória que esta tem.
        #
        # Três valores não são os originais, e é honesto dizer quais:
        #
        # ``surface-2`` era #FAFBFF, *mais claro* que a página. Ele é o hover
        # de quase todo cartão da aplicação de hoje, e um hover 1,4 de L* acima
        # de um cartão branco não existe para ninguém. Aqui ele desce para
        # baixo da página, como nas outras paletas, e o hover volta a acontecer.
        # ``surface-sunken`` desce junto para continuar sendo o degrau de baixo.
        #
        # As duas tintas de apoio reprovavam o AA: #64748B dava 4,2:1 sobre a
        # superfície rebaixada e #94A3B8 dava **2,4:1** sobre a página — o
        # cinza-azulado decorativo que o comentário de base.css ainda descreve.
        # Mesmo matiz, mesma família, escurecidos até se lerem.
        bg="#F6F7FB",
        surface="#FFFFFF",
        surface_2="#EDF0F8",
        surface_sunken="#E5E9F2",
        text="#111827",
        text_muted="#54647B",
        text_subtle="#5A697C",
        border="#E2E8F0",
        border_strong="#CBD5E1",
    ),
    dark=Surfaces(
        # O azul-marinho original. ``surface`` é o outro ajuste: #111827 ficava
        # 0,3 de L* acima de #0F172A, ou seja, o cartão tinha exatamente a cor
        # da página e só existia pela borda. Subiu para 10,9 — o mesmo degrau
        # que as outras paletas dão — e continua marinho.
        #
        # ``text-subtle`` subiu de #64748B (3,1:1 sobre o painel levantado)
        # até passar. O resto é literal.
        bg="#0F172A",
        surface="#151D2F",
        surface_2="#1E293B",
        surface_sunken="#0B1220",
        text="#F8FAFC",
        text_muted="#94A3B8",
        text_subtle="#8391A7",
        border="#334155",
        border_strong="#475569",
    ),
)


#: A ordem aqui é a ordem em que as paletas aparecem na tela. Verde fica no
#: meio de propósito: Ardósia e Dark são as duas paletas frias do conjunto, e
#: lado a lado no seletor elas se comparam em vez de se apresentarem.
PALETTES: tuple[Palette, ...] = (PAPEL, CARVAO, ARDOSIA, VERDE, DARK)

#: A paleta com que a aplicação abre, e o destino de qualquer chave inválida.
DEFAULT_PALETTE = PAPEL.key

_BY_KEY = {palette.key: palette for palette in PALETTES}


def get(key: str | None) -> Palette:
    """A paleta pedida, ou a padrão para qualquer coisa que não exista.

    Nunca levanta: a chave chega de uma coluna de configuração que pode ter
    sido escrita por uma versão anterior, editada à mão ou restaurada de um
    backup mais novo que este código. Uma paleta desconhecida vira a padrão,
    que é sempre uma tela utilizável.
    """
    return _BY_KEY.get((key or "").strip().lower(), PAPEL)


def choices() -> list[tuple[str, str]]:
    """As opções no formato que o WTForms espera."""
    return [(palette.key, palette.name) for palette in PALETTES]
