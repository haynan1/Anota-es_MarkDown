"""As paletas: o registro, a folha gerada e a reserva em CSS.

O contraste de cada paleta é medido em ``test_accessibility.py``, junto com
todo o resto do contraste da aplicação. Aqui ficam as garantias de sistema —
que a paleta escolhida realmente chega à tela, que uma chave impossível não
derruba nada, e que a cópia em ``base.css`` não envelheceu em silêncio.
"""

from __future__ import annotations

import re

import pytest

from app.services import palette as palettes
from app.services.accent import SOFT_ALPHA, build_ramp, contrast
from app.services.settings_service import SettingsService
from tests import contrast as measure

HEX = re.compile(r"^#[0-9A-F]{6}$")


class TestTheRegistry:
    def test_every_palette_is_complete_and_well_formed(self):
        for palette in palettes.PALETTES:
            assert palette.key and palette.name and palette.description
            assert HEX.match(palette.accent), f"{palette.key}: destaque malformado"
            for theme in ("light", "dark"):
                surfaces = getattr(palette, theme).as_css()
                assert len(surfaces) == 9, f"{palette.key}/{theme}: faltam superfícies"
                for name, value in surfaces.items():
                    assert HEX.match(value), f"{palette.key}/{theme}/{name}: {value}"

    def test_keys_are_unique(self):
        keys = [palette.key for palette in palettes.PALETTES]
        assert len(keys) == len(set(keys))

    def test_the_light_theme_is_lighter_than_the_dark_one(self):
        """Uma paleta trocada de lado é um erro de digitação caro."""
        for palette in palettes.PALETTES:
            light = measure.luminance(measure.channels(palette.light.bg)[:3])
            dark = measure.luminance(measure.channels(palette.dark.bg)[:3])
            assert light > dark, f"{palette.key}: os temas estão invertidos"

    def test_the_surfaces_stack_in_the_declared_order(self):
        """``sunken`` abaixo de ``bg`` abaixo de ``surface`` — nos dois temas.

        A regra é a mesma no claro e no escuro, e isso não é óbvio: "rebaixado"
        é sempre mais escuro e "levantado" é sempre mais claro, mesmo quando a
        página inteira já é preta. É essa ordem que permite a uma barra lateral
        parecer afundada e a um painel parecer levantado sem que nenhum dos
        dois precise de sombra — e uma paleta que a inverta quebra a metáfora
        inteira em silêncio, porque cada cor isolada continua bonita.
        """
        for palette in palettes.PALETTES:
            for theme in ("light", "dark"):
                surfaces = getattr(palette, theme)
                steps = [
                    measure.luminance(measure.channels(colour)[:3])
                    for colour in (
                        surfaces.surface_sunken,
                        surfaces.bg,
                        surfaces.surface,
                    )
                ]
                assert steps == sorted(steps), (
                    f"{palette.key}/{theme}: as superfícies não se empilham "
                    "(rebaixada, página, painel devem ir do mais escuro ao mais claro)"
                )

    @pytest.mark.parametrize("key", ["", "   ", "nao-existe", None, "PAPEL"])
    def test_an_unknown_key_falls_back_to_the_default(self, key):
        """A chave chega de uma coluna que um backup mais novo pode ter escrito."""
        assert palettes.get(key).key in {palettes.DEFAULT_PALETTE, "papel"}

    def test_the_default_palette_exists(self):
        assert palettes.get(palettes.DEFAULT_PALETTE).key == palettes.DEFAULT_PALETTE

    def test_the_swatch_names_the_two_themes_and_the_accent(self):
        for palette in palettes.PALETTES:
            dark, light, accent = palette.swatch
            assert dark == palette.dark.bg
            assert light == palette.light.bg
            assert accent == palette.accent


class TestTheAccentClearsEveryPalette:
    """O solver é medido contra a superfície mais difícil de cada tema.

    Não a média: o destaque é escrito sobre o próprio tom -soft em etiquetas,
    linhas selecionadas e alvos de arrastar, e esse tom puxa o fundo em
    direção à tinta. Resolver só contra a superfície lisa foi exatamente o que
    deixou um ouro passar a 5,0:1 e chegar a 4,1:1 na etiqueta que o usava.
    """

    AA = 4.5

    @pytest.mark.parametrize("palette", palettes.PALETTES, ids=lambda p: p.key)
    def test_the_accent_is_readable_on_surface_and_on_its_own_tint(self, palette):
        ramp = build_ramp(
            palette.accent, palette.light.surface_sunken, palette.dark.surface_2
        )

        cases = [
            ("light", ramp.light, palette.light, SOFT_ALPHA["light"]),
            ("dark", ramp.dark, palette.dark, SOFT_ALPHA["dark"]),
        ]
        for theme, accent, surfaces, alpha in cases:
            for name, ground in surfaces.as_css().items():
                if not name.startswith(("bg", "surface")):
                    continue
                plain = contrast(accent, ground)
                assert plain >= self.AA, (
                    f"{palette.key}/{theme}: destaque sobre --{name} a {plain:.2f}:1"
                )

                tint = measure.resolve(
                    {
                        "soft": "rgba(%d, %d, %d, %s)"
                        % (*measure.channels(accent)[:3], alpha),
                        "under": ground,
                    },
                    "soft",
                    "under",
                )
                tinted = measure.ratio(measure.channels(accent)[:3], tint)
                assert tinted >= self.AA, (
                    f"{palette.key}/{theme}: destaque sobre o próprio tom "
                    f"em --{name} a {tinted:.2f}:1"
                )

    @pytest.mark.parametrize("palette", palettes.PALETTES, ids=lambda p: p.key)
    def test_a_primary_button_label_is_readable(self, palette):
        ramp = build_ramp(
            palette.accent, palette.light.surface_sunken, palette.dark.surface_2
        )
        for accent, ink in (
            (ramp.light, ramp.light_contrast),
            (ramp.dark, ramp.dark_contrast),
        ):
            value = contrast(ink, accent)
            assert value >= self.AA, f"{palette.key}: rótulo a {value:.2f}:1"


class TestTheGeneratedSheet:
    """O que a folha em /assets/theme.css realmente entrega."""

    def sheet(self, client) -> str:
        response = client.get("/assets/theme.css")
        assert response.status_code == 200
        assert response.mimetype == "text/css"
        return response.data.decode("utf-8")

    def test_it_carries_the_whole_palette_and_not_only_the_accent(self, client, app):
        """A regressão que este teste guarda: emitir só o destaque de novo.

        Foi assim durante toda a vida anterior desta folha, e é o que fazia
        "trocar de tema" trocar a cor de um botão sobre um fundo que continuava
        sendo o de outra pessoa.
        """
        css = self.sheet(client)
        for token in (
            "--bg",
            "--surface",
            "--surface-2",
            "--surface-sunken",
            "--text",
            "--text-muted",
            "--text-subtle",
            "--border",
            "--border-strong",
            "--accent",
        ):
            assert f"{token}:" in css, f"{token} não é emitido"

    @pytest.mark.parametrize("palette", palettes.PALETTES, ids=lambda p: p.key)
    def test_the_chosen_palette_is_the_one_served(self, client, app, palette):
        with app.app_context():
            SettingsService.update_many({"palette": palette.key})

        css = self.sheet(client)
        assert f"--bg: {palette.light.bg};" in css
        assert f"--bg: {palette.dark.bg};" in css

    def test_an_impossible_key_still_renders_a_usable_screen(self, client, app):
        """Um valor que nenhum rádio consegue produzir, mas um backup consegue."""
        with app.app_context():
            SettingsService.update_many({"palette": "paleta-do-futuro"})

        css = self.sheet(client)
        assert f"--bg: {palettes.PAPEL.light.bg};" in css

    def test_the_soft_tint_uses_the_alpha_the_solver_assumed(self, client, app):
        """Se os dois divergirem, a cor foi calculada para um fundo que não existe."""
        css = self.sheet(client)
        assert f"{SOFT_ALPHA['light'] * 100}%" in css
        assert f"{SOFT_ALPHA['dark'] * 100}%" in css

    def test_the_accent_stays_the_users_across_a_palette_change(self, client, app):
        """A paleta diz com qual cor nasce; ela não confisca a que foi escolhida."""
        with app.app_context():
            SettingsService.update_many(
                {"palette": palettes.CARVAO.key, "accent_color": "#3366FF"}
            )

        css = self.sheet(client)
        assert "--accent-seed:     #3366FF;" in css


class TestTheCssFallbackHasNotDrifted:
    """``base.css`` declara a paleta padrão como reserva, e reserva envelhece.

    É o que se vê no instante antes da folha gerada chegar, e o que se veria se
    ela nunca chegasse — então os dois valores têm de ser o mesmo valor. Este
    teste é a única coisa que impede a cópia em CSS de virar uma paleta
    fantasma que ninguém escolheu e todo mundo vê por um quadro.
    """

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_base_css_matches_the_default_palette(self, theme):
        declared = measure.css_tokens(theme)
        expected = getattr(palettes.get(palettes.DEFAULT_PALETTE), theme).as_css()

        for name, value in expected.items():
            assert name in declared, f"--{name} sumiu de base.css ({theme})"
            assert declared[name].upper() == value.upper(), (
                f"--{name} no tema {theme}: base.css diz {declared[name]}, "
                f"a paleta padrão diz {value}"
            )


class TestTheSettingsScreen:
    """O caminho que o usuário realmente percorre: o formulário."""

    BASE = {
        "app_name": "Markdown Studio",
        "theme": "dark",
        "accent_color": "#16A34A",
        "timezone": "America/Sao_Paulo",
        "autosave_seconds": "3",
        "pdf_page_size": "A4",
        "pdf_theme": "classic",
        "pdf_font": "serif",
        "pdf_margin": "normal",
        "backup_keep_last": "10",
    }

    def post(self, client, **extra):
        response = client.post(
            "/configuracoes/", data={**self.BASE, **extra}, follow_redirects=True
        )
        assert response.status_code == 200
        SettingsService.invalidate_cache()
        return response

    def test_the_picker_offers_every_palette(self, client):
        html = client.get("/configuracoes/").data.decode("utf-8")
        for palette in palettes.PALETTES:
            assert f'value="{palette.key}"' in html, f"{palette.key} não aparece"
            assert palette.name in html
            # A miniatura chega por data-color, que é o mecanismo que a CSP
            # permite; um style= aqui seria bloqueado sem aviso.
            assert f'data-color="{palette.dark.bg}"' in html

    def test_choosing_a_palette_saves_it(self, client, app):
        self.post(client, palette=palettes.CARVAO.key)
        assert SettingsService.get("palette") == palettes.CARVAO.key

    def test_going_back_saves_it_too(self, client, app):
        self.post(client, palette=palettes.CARVAO.key)
        self.post(client, palette=palettes.PAPEL.key)
        assert SettingsService.get("palette") == palettes.PAPEL.key

    def test_a_submission_without_the_field_leaves_the_palette_alone(self, client, app):
        """Ausente quer dizer "não mexa", e não "volte para a padrão".

        Um cliente antigo, um script ou um formulário montado à mão não devem
        derrubar a paleta escolhida só por não falarem dela.
        """
        self.post(client, palette=palettes.CARVAO.key)
        self.post(client)  # sem o campo
        assert SettingsService.get("palette") == palettes.CARVAO.key

    def test_an_impossible_value_does_not_reach_the_database(self, client, app):
        """O rádio não consegue produzir isto; um POST à mão consegue."""
        self.post(client, palette="../../etc/passwd")
        assert SettingsService.get("palette") in {p.key for p in palettes.PALETTES}

    def test_the_rest_of_the_form_still_saves_alongside_it(self, client, app):
        """A paleta entrou no meio de um formulário que já funcionava."""
        self.post(client, palette=palettes.CARVAO.key, app_name="Meu Estúdio")
        assert SettingsService.get("app_name") == "Meu Estúdio"
        assert SettingsService.get("palette") == palettes.CARVAO.key
