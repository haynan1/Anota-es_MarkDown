"""Um mapa saindo em PDF, PNG e JPEG - e sendo o mesmo mapa nos três.

O risco que este arquivo existe para prender não é "o arquivo não abre". É o
outro: quatro exportações que quase concordam. Uma quebra de linha diferente no
PDF, uma caixa um pixel fora no PNG, uma curva que virou reta - defeitos que só
aparecem quando alguém põe dois arquivos lado a lado num slide, meses depois.

Por isso o que se afirma aqui é sobre a *cena*: os três motores recebem a mesma
e não podem discordar dela. O resto são as promessas que um download tem de
cumprir - anexo, tipo declarado, nome de arquivo derivado do título - e os
tetos que impedem um mapa enorme de virar uma alocação de gigabytes.
"""

from __future__ import annotations

import io
import uuid as uuid_module

import pytest
from PIL import Image

from app.repositories.mind_map_repository import MindMapRepository
from app.services.exceptions import ValidationError
from app.services.mind_map_drawing import (
    EMPTY_MESSAGE,
    PAPER,
    Card,
    build_scene,
    rgb_of,
    wrap_label,
)
from app.services.mind_map_layout import Box, CurveTo, LineTo, MoveTo, QuadTo
from app.services.mind_map_picture import (
    MAX_INTERMEDIATE_PIXELS,
    MAX_RASTER_SIDE,
    SUPERSAMPLE,
    _as_cubic,
    _dashed,
    _faded,
    _flatten,
    _font,
    raster_scale,
    to_jpeg,
    to_pdf,
    to_png,
)
from app.services.mind_map_service import MindMapService

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


@pytest.fixture()
def mind_map(app):
    """Um mapa com o suficiente para exercitar todo primitivo do desenho."""
    mind_map = MindMapService.create("Lançamento — Ação 2026", "Coração do plano")
    root = MindMapRepository.nodes_of(mind_map)[0]

    def add(parent, **fields):
        identifier = str(uuid_module.uuid4())
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.create", "uuid": identifier, "parent": parent, "fields": fields}],
        )
        return identifier

    pesquisa = add(root.uuid, text="Pesquisa de opinião", color="#0EA5E9")
    engenharia = add(root.uuid, text="Engenharia", color="#22C55E", shape="rect")
    add(pesquisa, text="Entrevistas", url="https://exemplo.test/pauta")
    observabilidade = add(engenharia, text="Observabilidade")
    # Um espelho: a linha tracejada que só existe no desenho.
    add(pesquisa, mirror_of=observabilidade)
    MindMapService.autolayout(mind_map, "right")
    return mind_map


@pytest.fixture()
def scene(mind_map):
    return build_scene(mind_map, MindMapRepository.nodes_of(mind_map))


def opened(picture) -> Image.Image:
    return Image.open(io.BytesIO(picture.data))


def ink_ratio(image: Image.Image) -> float:
    """Que fração da figura não é papel. Zero é uma folha em branco."""
    paper = rgb_of(PAPER)
    pixels = list(image.convert("RGB").getdata())
    return sum(1 for pixel in pixels if pixel != paper) / len(pixels)


# ── Os arquivos ─────────────────────────────────────────────────────────────


class TestEachFormatIsItself:
    def test_the_pdf_is_a_pdf(self, app, scene):
        picture = to_pdf(scene)
        assert picture.data.startswith(PDF_MAGIC)
        assert picture.mimetype == "application/pdf"
        assert picture.extension == ".pdf"

    def test_the_png_is_a_png(self, app, scene):
        picture = to_png(scene)
        assert picture.data.startswith(PNG_MAGIC)
        assert opened(picture).format == "PNG"

    def test_the_jpeg_is_a_jpeg(self, app, scene):
        picture = to_jpeg(scene)
        assert picture.data.startswith(JPEG_MAGIC)
        assert opened(picture).format == "JPEG"

    def test_the_pdf_is_one_page_the_size_of_the_drawing(self, app, scene):
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(to_pdf(scene).data))
        assert len(reader.pages) == 1

        box = reader.pages[0].mediabox
        assert float(box.width) == pytest.approx(scene.width, abs=1.0)
        assert float(box.height) == pytest.approx(scene.height, abs=1.0)

    def test_the_pdf_carries_the_title_and_the_words(self, app, scene):
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(to_pdf(scene).data))
        assert reader.metadata["/Title"] == "Lançamento — Ação 2026"

        text = reader.pages[0].extract_text()
        assert "Engenharia" in text
        assert "Observabilidade" in text

    def test_the_pdf_embeds_the_face_rather_than_hoping_for_one(self, app, scene):
        """Base-14 é WinAnsi. Um mapa em polonês sairia como bytes trocados."""
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(to_pdf(scene).data))
        fonts = reader.pages[0]["/Resources"]["/Font"]
        embedded = [
            font
            for font in fonts.values()
            if "/FontDescriptor" in font.get_object()
            or "/DescendantFonts" in font.get_object()
        ]
        assert embedded, "o PDF não embutiu nenhuma fonte"


class TestBothPicturesAreOneDrawing:
    def test_png_and_jpeg_have_the_same_frame(self, app, scene):
        assert opened(to_png(scene)).size == opened(to_jpeg(scene)).size

    def test_the_frame_is_the_scene_at_the_chosen_scale(self, app, scene):
        width, height = opened(to_png(scene)).size
        scale = raster_scale(scene)
        assert width == pytest.approx(round(scene.width * scale), abs=1)
        assert height == pytest.approx(round(scene.height * scale), abs=1)

    def test_the_drawing_is_not_a_blank_sheet(self, app, scene):
        assert ink_ratio(opened(to_png(scene))) > 0.01

    def test_the_paper_shows_at_the_corners(self, app, scene):
        """O enquadramento tem folga, e a folga é papel - não corte."""
        image = opened(to_png(scene)).convert("RGB")
        width, height = image.size
        for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            assert image.getpixel(corner) == rgb_of(PAPER)

    def test_the_map_colour_reaches_the_picture(self, app, mind_map, scene):
        """A cor predominante tem de estar lá, e sem mistura nenhuma.

        Lida do mapa e não do primeiro cartão: se um dia o centro deixar de
        vestir o acento, esta asserção tem de falhar em vez de passar contra a
        cor branca do papel.
        """
        image = opened(to_png(scene)).convert("RGB")
        assert rgb_of(mind_map.color) in set(image.getdata())

    def test_only_the_centre_wears_the_accent(self, app, mind_map, scene):
        """Um tópico solto não é um segundo centro.

        Todo nó sem pai vestia a cor do mapa, então um ramo recém-desconectado
        aparecia na figura anunciando-se como o assunto principal - enquanto na
        tela ele continuava um tópico comum. A figura e o quadro dizem a mesma
        coisa agora.
        """
        accent = mind_map.color
        wearing = [card for card in scene.cards if card.fill == accent]

        assert len(wearing) == 1
        assert wearing[0].strong is True
        assert sum(1 for card in scene.cards if card.strong) == 1


# ── O mapa vazio ────────────────────────────────────────────────────────────


class TestAMapWithNothingInIt:
    @pytest.fixture()
    def bare(self, app, mind_map):
        root = MindMapRepository.nodes_of(mind_map)[0]
        MindMapService.apply_operations(
            mind_map, [{"type": "node.delete", "uuid": root.uuid}]
        )
        return build_scene(mind_map, MindMapRepository.nodes_of(mind_map))

    def test_it_says_so_instead_of_failing(self, app, bare):
        assert bare.message == EMPTY_MESSAGE
        assert bare.cards == ()

    @pytest.mark.parametrize("draw", [to_pdf, to_png, to_jpeg])
    def test_every_format_still_produces_a_file(self, app, bare, draw):
        assert draw(bare).data

    def test_the_message_is_actually_drawn(self, app, bare):
        assert ink_ratio(opened(to_png(bare))) > 0.0


# ── Os tetos ────────────────────────────────────────────────────────────────


class TestNothingUnbounded:
    def test_an_enormous_board_is_scaled_down_rather_than_allocated(self, app, scene):
        """Um mapa espalhado por cem mil unidades não vira um bitmap de GB.

        O teto é sobre o buffer intermediário - o maior objeto que este módulo
        chega a segurar - e não sobre a imagem final, que é um quarto dele.
        """
        from dataclasses import replace

        huge = replace(scene, width=100_000.0, height=80_000.0)
        scale = raster_scale(huge)

        assert huge.width * scale <= MAX_RASTER_SIDE
        assert huge.height * scale <= MAX_RASTER_SIDE
        buffer_pixels = (
            huge.width * scale * SUPERSAMPLE * huge.height * scale * SUPERSAMPLE
        )
        assert buffer_pixels <= MAX_INTERMEDIATE_PIXELS + 1

    def test_a_wide_board_is_capped_on_its_long_side(self, app, scene):
        from dataclasses import replace

        wide = replace(scene, width=60_000.0, height=400.0)
        assert wide.width * raster_scale(wide) <= MAX_RASTER_SIDE

    def test_an_ordinary_board_gets_the_full_scale(self, app, scene):
        assert raster_scale(scene) == pytest.approx(2.0)


# ── A geometria que os motores compartilham ─────────────────────────────────


class TestTheGeometryIsTranscribedExactly:
    def test_a_quadratic_becomes_the_cubic_that_draws_the_same_curve(self):
        """Elevação de grau, não aproximação.

        Um cotovelo arredondado é um `Q`, e o canto que ele desenha tem de ser
        o mesmo canto nos três formatos. A prova é o ponto médio: a cúbica
        elevada passa exatamente por onde a quadrática passava.
        """
        start = (0.0, 0.0)
        segment = QuadTo(10.0, 0.0, 10.0, 10.0)
        first, second, end = _as_cubic(segment, start)

        def cubic(t):
            rest = 1 - t
            return tuple(
                rest**3 * a + 3 * rest**2 * t * b + 3 * rest * t**2 * c + t**3 * d
                for a, b, c, d in zip(start, first, second, end, strict=True)
            )

        def quadratic(t):
            rest = 1 - t
            return tuple(
                rest**2 * a + 2 * rest * t * b + t**2 * c
                for a, b, c in zip(start, (10.0, 0.0), end, strict=True)
            )

        for step in range(11):
            t = step / 10
            assert cubic(t) == pytest.approx(quadratic(t))

    def test_a_curve_is_flattened_finely_enough_to_look_curved(self):
        segments = (MoveTo(0.0, 0.0), CurveTo(60.0, 0.0, 60.0, 100.0, 120.0, 100.0))
        points = _flatten(segments, unit=2.0)

        assert len(points) > 20
        assert points[0] == (0.0, 0.0)
        assert points[-1] == pytest.approx((120.0, 100.0))

    def test_a_straight_run_is_left_alone(self):
        points = _flatten((MoveTo(0.0, 0.0), LineTo(10.0, 0.0)), unit=2.0)
        assert points == [(0.0, 0.0), (10.0, 0.0)]

    def test_a_dashed_line_becomes_alternating_runs(self):
        """Pillow não tem tracejado, e o tracejado aqui carrega significado."""
        runs = _dashed([(0.0, 0.0), (100.0, 0.0)], unit=1.0)

        assert len(runs) > 1
        drawn = sum(abs(run[-1][0] - run[0][0]) for run in runs)
        # Seis ligados para cada cinco desligados: pouco mais da metade.
        assert 0.4 < drawn / 100.0 < 0.75

    def test_a_solid_line_would_be_the_whole_length(self):
        assert _dashed([(0.0, 0.0), (10.0, 0.0)], unit=100.0) == [
            [(0.0, 0.0), (10.0, 0.0)]
        ]

    def test_a_zero_length_step_does_not_divide_by_it(self):
        """Dois nós exatamente no mesmo ponto acontecem, e não podem explodir."""
        assert _dashed([(5.0, 5.0), (5.0, 5.0)], unit=1.0) is not None


class TestTheConnectionColourIsMixedNotFaked:
    def test_it_lands_between_the_ink_and_the_paper(self):
        faded = _faded("#000000", "#FFFFFF")
        red, green, blue = rgb_of(faded)
        assert red == green == blue
        # 45% de papel branco por cima de tinta preta.
        assert red == pytest.approx(115, abs=2)

    def test_ink_on_its_own_paper_stays_that_ink(self):
        assert rgb_of(_faded("#FFFFFF", "#FFFFFF")) == (255, 255, 255)

    def test_the_short_hex_form_is_understood(self):
        assert rgb_of("#0f8") == (0, 255, 136)


# ── O tipo ──────────────────────────────────────────────────────────────────


class TestTheFaceCanSpellPortuguese:
    @pytest.mark.parametrize("letter", ["ç", "ã", "õ", "é", "Á", "ê", "…", "—"])
    def test_every_letter_a_map_will_contain_has_a_glyph(self, letter):
        """A face embutida do Pillow não tem `ç`, e um mapa em português saía
        cheio de quadradinhos vazios. O teste é a lembrança disso."""
        assert _font(28.0).getmask(letter).getbbox() is not None, letter

    def test_the_bold_face_can_spell_it_too(self):
        assert _font(28.0, strong=True).getmask("ção").getbbox() is not None

    def test_a_long_label_is_set_small_enough_to_stay_inside_its_box(self, app, scene):
        """A quebra é estimada por largura média, e a estimativa erra.

        A prova é indireta e é a que importa: nenhuma linha desenhada é mais
        larga do que a área interna da caixa em que ela mora.
        """
        from app.services.mind_map_drawing import PADDING_X
        from app.services.mind_map_picture import _fitted_size

        for card in scene.cards:
            if not card.lines:
                continue
            size = _fitted_size(card, lambda text, at: _font(at).getlength(text))
            widest = max(_font(size).getlength(line) for line in card.lines)
            assert widest <= card.width - PADDING_X * 2 + 1


class TestWrappingIsDecidedOnceForEverybody:
    def test_a_short_label_is_one_line(self):
        assert wrap_label("Engenharia", 180.0) == ("Engenharia",)

    def test_a_long_label_is_cut_with_an_ellipsis_rather_than_spilling(self):
        lines = wrap_label(" ".join(["palavra"] * 60), 180.0)
        assert len(lines) == 4
        assert lines[-1].endswith("…")

    def test_an_empty_label_is_no_lines_at_all(self):
        assert wrap_label("", 180.0) == ()


# ── Pela web ────────────────────────────────────────────────────────────────


class TestTheDownloads:
    @pytest.mark.parametrize(
        "fmt,mimetype,extension",
        [
            ("pdf", "application/pdf", ".pdf"),
            ("png", "image/png", ".png"),
            ("jpeg", "image/jpeg", ".jpg"),
        ],
    )
    def test_each_format_downloads_as_an_attachment(
        self, app, client, mind_map, fmt, mimetype, extension
    ):
        response = client.get(f"/mapas/{mind_map.uuid}/exportar/{fmt}")

        assert response.status_code == 200
        assert response.mimetype == mimetype
        disposition = response.headers["Content-Disposition"]
        assert disposition.startswith("attachment")
        # O nome vem do slug do título, nunca do título cru.
        assert f"lancamento-acao-2026{extension}" in disposition
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.data

    def test_an_unknown_format_is_refused_rather_than_guessed(self, app, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}/exportar/bmp")
        assert response.status_code == 400

    def test_a_format_that_looks_like_a_path_is_refused(self, app, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}/exportar/..%5cetc")
        assert response.status_code in {400, 404}

    def test_the_service_refuses_an_unknown_format_before_drawing(self, app, mind_map):
        with pytest.raises(ValidationError):
            MindMapService.export_picture(mind_map, "tiff")

    def test_a_missing_map_is_a_404(self, app, client):
        response = client.get(f"/mapas/{uuid_module.uuid4()}/exportar/png")
        assert response.status_code == 404

    def test_the_canvas_offers_all_five_formats(self, app, client, mind_map):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)
        for suffix in ("markdown", "exportar/pdf", "exportar/png", "exportar/jpeg", "svg"):
            assert f"/mapas/{mind_map.uuid}/{suffix}" in html

    def test_the_gallery_offers_them_too(self, app, client, mind_map):
        html = client.get("/mapas/").get_data(as_text=True)
        assert f"/mapas/{mind_map.uuid}/exportar/pdf" in html
        assert f"/mapas/{mind_map.uuid}/exportar/jpeg" in html


# ── A cena, que é a fonte de todos eles ─────────────────────────────────────


class TestTheSceneIsTheSingleSource:
    def test_the_svg_is_framed_exactly_like_the_scene(self, app, mind_map, scene):
        svg = MindMapService.export_svg(mind_map)
        assert f'width="{scene.width:.0f}"' in svg
        assert f'height="{scene.height:.0f}"' in svg

    def test_a_mirror_is_a_line_and_never_a_box(self, app, scene):
        """Um espelho aparece como caminho tracejado até a caixa do original."""
        assert any(connection.shared for connection in scene.connections)
        # Cinco nós, um deles espelho: quatro caixas.
        assert len(scene.cards) == 5

    def test_a_topic_with_a_link_is_flagged(self, app, scene):
        assert any(card.flagged for card in scene.cards)

    def test_the_frame_leaves_room_around_the_outermost_box(self, app, scene):
        left = min(card.x for card in scene.cards)
        assert scene.x < left

    def test_a_corner_radius_never_exceeds_the_box(self, app, scene):
        from app.services.mind_map_picture import _corner

        for card in scene.cards:
            assert _corner(card) <= min(card.width, card.height) / 2 + 1e-9


class TestTheStraightRoutingsReachThePdfToo:
    """O organograma e o radial desenham retas, e reta é outro caminho no PDF.

    A cobertura pegou isto: todo teste de PDF usava um mapa horizontal, e um
    mapa horizontal é feito só de curvas. O ramo que escreve ``lineTo`` nunca
    tinha rodado - o formato que mais se imprime, no arranjo que mais se
    imprime, saindo por código que ninguém tinha executado.
    """

    @pytest.fixture()
    def elbows(self, app, mind_map):
        MindMapService.autolayout(mind_map, "tree")
        return build_scene(mind_map, MindMapRepository.nodes_of(mind_map))

    @pytest.fixture()
    def spokes(self, app, mind_map):
        MindMapService.autolayout(mind_map, "radial")
        return build_scene(mind_map, MindMapRepository.nodes_of(mind_map))

    def test_an_org_chart_has_straight_runs_in_it(self, app, elbows):
        kinds = {
            type(segment)
            for connection in elbows.connections
            for segment in connection.segments
        }
        assert LineTo in kinds

    @pytest.mark.parametrize("arrangement", ["elbows", "spokes"])
    @pytest.mark.parametrize("draw", [to_pdf, to_png, to_jpeg])
    def test_every_format_draws_them(self, app, request, arrangement, draw):
        scene = request.getfixturevalue(arrangement)
        assert draw(scene).data

    def test_the_pdf_still_carries_the_words(self, app, elbows):
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(to_pdf(elbows).data))
        assert "Engenharia" in reader.pages[0].extract_text()


class TestWhenTheFaceIsNotWhereItShouldBe:
    """Uma instalação enxuta pode não ter os arquivos do ReportLab.

    O caminho de degradação existia e nunca tinha rodado, que é a definição de
    um fallback em que não se pode confiar. Aqui ele roda: a figura sai mais
    simples, e sai.
    """

    @pytest.fixture()
    def faceless(self, monkeypatch):
        from app.services import mind_map_picture

        mind_map_picture._font_at.cache_clear()
        mind_map_picture._pdf_face.cache_clear()
        monkeypatch.setattr(mind_map_picture, "_face_path", lambda strong: None)
        yield
        mind_map_picture._font_at.cache_clear()
        mind_map_picture._pdf_face.cache_clear()

    def test_the_bitmap_falls_back_to_a_bundled_face(self, app, faceless, scene):
        assert _font(24.0) is not None
        assert ink_ratio(opened(to_png(scene))) > 0.01

    def test_the_pdf_falls_back_to_base_14(self, app, faceless, scene):
        from app.services.mind_map_picture import (
            PDF_FALLBACK_BOLD,
            PDF_FALLBACK_REGULAR,
            _pdf_face,
        )

        assert _pdf_face(strong=False) == PDF_FALLBACK_REGULAR
        assert _pdf_face(strong=True) == PDF_FALLBACK_BOLD
        assert to_pdf(scene).data.startswith(PDF_MAGIC)


class TestFittingDegenerateBoxes:
    def test_a_box_with_no_room_inside_it_keeps_the_default_size(self):
        """Nada a caber em nada: encolher não resolveria, então não encolhe."""
        from app.services.mind_map_drawing import FONT_SIZE
        from app.services.mind_map_picture import _fitted_size

        narrow = Card(
            x=0.0, y=0.0, width=8.0, height=20.0, radius=2.0, fill="#FFFFFF",
            stroke="#CBD5E1", lines=("Tópico",), text_colour="#111827",
            strong=False, flagged=False,
        )
        assert _fitted_size(narrow, lambda text, at: 999.0) == FONT_SIZE

    def test_a_box_with_no_words_keeps_it_too(self):
        from app.services.mind_map_drawing import FONT_SIZE
        from app.services.mind_map_picture import _fitted_size

        blank = Card(
            x=0.0, y=0.0, width=180.0, height=48.0, radius=12.0, fill="#FFFFFF",
            stroke="#CBD5E1", lines=(), text_colour="#111827",
            strong=False, flagged=False,
        )
        assert _fitted_size(blank, lambda text, at: 999.0) == FONT_SIZE

    def test_shrinking_stops_before_the_type_becomes_a_defect(self):
        from app.services.mind_map_drawing import FONT_SIZE
        from app.services.mind_map_picture import MIN_FIT_RATIO, _fitted_size

        wide = Card(
            x=0.0, y=0.0, width=180.0, height=48.0, radius=12.0, fill="#FFFFFF",
            stroke="#CBD5E1", lines=("qualquer coisa",), text_colour="#111827",
            strong=False, flagged=False,
        )
        # Uma linha dez vezes mais larga do que a caixa: o piso é que responde.
        assert _fitted_size(wide, lambda text, at: 2000.0) == pytest.approx(
            FONT_SIZE * MIN_FIT_RATIO
        )


class TestOnlyAHexLiteralIsEverDrawn:
    """Segunda barreira. O serviço já recusa, e a figura recusa de novo."""

    @pytest.mark.parametrize(
        "hostile",
        ["#zzzzzz", "#gg0", "red", "url(javascript:alert(1))", "", "#12345", None],
    )
    def test_anything_that_is_not_one_becomes_the_fallback(self, hostile):
        from app.services.mind_map_drawing import clean_colour

        assert clean_colour(hostile, "#4F46E5") == "#4F46E5"

    @pytest.mark.parametrize("valid", ["#000", "#FFFFFF", "#0ea5e9", "#0EA5E9"])
    def test_a_real_one_survives_and_parses(self, valid):
        from app.services.mind_map_drawing import clean_colour

        assert clean_colour(valid, "#4F46E5") == valid
        assert len(rgb_of(valid)) == 3


class TestTheSegmentsStillWriteTheSamePath:
    """A refatoração que trouxe a geometria estruturada não podia mudar o `d`.

    A tela desenha as mesmas curvas em JavaScript, e a paridade entre as duas
    é comparada caractere a caractere em ``tests/js``. Este é o lado de cá.
    """

    @pytest.mark.parametrize("routing", ["horizontal", "vertical", "elbow", "spoke"])
    def test_the_path_is_built_from_the_segments(self, routing):
        from app.services.mind_map_layout import (
            branch_path,
            branch_segments,
            segments_to_path,
        )

        parent = Box(0.0, 0.0, 180.0, 48.0)
        child = Box(300.0, 120.0, 160.0, 44.0)
        assert branch_path(routing, parent, child) == segments_to_path(
            branch_segments(routing, parent, child)
        )
