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
    accent="#0F6E64",
    light=Surfaces(
        bg="#F5F2EC",
        surface="#FDFCFA",
        surface_2="#EFEBE3",
        surface_sunken="#E7E2D8",
        text="#1A1712",
        text_muted="#5C5548",
        text_subtle="#686155",
        border="#E0DACE",
        border_strong="#C6BFB0",
    ),
    dark=Surfaces(
        bg="#131110",
        surface="#1B1815",
        surface_2="#262119",
        surface_sunken="#0B0A09",
        text="#F2EEE7",
        text_muted="#A39C90",
        text_subtle="#948D80",
        border="#35302A",
        border_strong="#4B453B",
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


#: A ordem aqui é a ordem em que as paletas aparecem na tela.
PALETTES: tuple[Palette, ...] = (PAPEL, CARVAO)

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
