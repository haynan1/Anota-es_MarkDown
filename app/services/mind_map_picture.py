"""O mapa como página e como imagem: PDF, PNG e JPEG.

Two backends, one drawing
-------------------------
Both read the :class:`~app.services.mind_map_drawing.Scene` the SVG exporter
reads, and neither decides anything about it. Where a box sits, how a label
broke, which line is dashed - all of that was settled in ``mind_map_drawing``
before either of these was called, which is the whole reason the four exports
of one map are four views of the same picture instead of four near-misses.

Why not rasterise the SVG
-------------------------
The obvious route is to render the SVG this application already produces.
Every library that does it needs Cairo, and Cairo is a native dependency that
is not on a Windows box unless somebody installed GTK by hand - the same wall
WeasyPrint hits in ``pdf_service``, and the reason this application carries a
pure-Python PDF engine at all. Rasterising in the browser instead would have
put the export behind JavaScript and made it untestable without one.

So the drawing is transcribed twice more, against two libraries that ship as
wheels on every platform: ReportLab draws the PDF as real vector, Pillow paints
the bitmap. Neither reaches the network, the filesystem or the database.

One face, and it is not the machine's
------------------------------------
Both backends set the label in Bitstream Vera Sans, which ships inside
ReportLab - already a hard dependency here - so it is the same file on every
platform and there is no binary to check into this repository for it.

Probing the host for "Segoe UI, or Arial, or DejaVu" was the alternative, and
it would have made one installation's export a different picture from
another's. Pillow's own bundled face was the other, and it turned out to have
no ``ç`` and no ``ã``: a Portuguese mind map came out full of empty boxes,
which is the kind of defect that only ever shows up in the finished file.

The label is fitted rather than trusted. Wrapping is estimated from an average
character width (see ``mind_map_drawing``), and an estimate is occasionally
wrong by a word; a line measured wider than the box it sits in is set a little
smaller instead of being allowed to run over the edge.

Everything a hostile map could ask for is bounded
-------------------------------------------------
A board may be a hundred thousand units across, and a bitmap of it at two
pixels per unit is an allocation nobody should be able to request over HTTP.
There is therefore one budget - the size of the antialiasing buffer, which is
the largest thing this module ever holds - and the scale is whatever fits
inside it. Resolution is what gives way, because a slightly smaller picture
that is still smooth beats a larger one with staircase edges.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from app.services.mind_map_drawing import (
    FONT_SIZE,
    INK_MUTED,
    LINE_HEIGHT,
    LINK,
    MARKER_INSET,
    MARKER_RADIUS,
    PADDING_X,
    Card,
    Connection,
    Scene,
    rgb_of,
)
from app.services.mind_map_layout import CurveTo, LineTo, MoveTo, QuadTo, Segment

if TYPE_CHECKING:  # pragma: no cover - import cost avoided at runtime
    from reportlab.pdfgen.canvas import Canvas

logger = logging.getLogger(__name__)

PRODUCER = "Markdown Studio"

Point = tuple[float, float]

# How thick a connection and a box outline are drawn, in scene units. Named
# here rather than in the scene because they are a property of the pen, and
# each backend scales them by its own factor.
CONNECTION_WIDTH = 2.0
CARD_OUTLINE_WIDTH = 1.0
# The board draws its connections at 55% so a dense map does not turn into a
# thicket. There is no per-shape opacity in either backend, and there does not
# need to be: connections are painted first, on bare paper, so mixing the ink
# with the paper by hand produces exactly the same pixel.
CONNECTION_OPACITY = 0.55
# "6 on, 5 off" - the same dash the SVG writes for a second path to a topic
# that lives elsewhere.
DASH_ON = 6.0
DASH_OFF = 5.0

# A PDF page cannot exceed 200 inches on a side. A map big enough to hit that
# is scaled down whole rather than cropped: a picture of most of a mind map is
# not a picture of the mind map.
MAX_PAGE_POINTS = 14_400.0

# Two device pixels per scene unit, so the picture holds up when it is dropped
# into a slide and scaled back up.
RASTER_SCALE = 2.0
# Neither Pillow's rounded rectangle nor its line drawing is antialiased, so
# the picture is painted at twice the requested size and resampled down. That
# is where the smooth edges come from.
SUPERSAMPLE = 2
# The one ceiling, and it is on that intermediate buffer because it is the
# largest thing this module ever allocates - the finished image is a quarter
# of it. Forty-eight megapixels is around 144 MB of RGB, which is a request
# a person can make of their own machine without thinking about it.
#
# When a board does not fit, the *scale* gives way and the antialiasing stays.
# The other way round - full resolution, no smoothing past a threshold - would
# mean a map that silently changes quality as it grows, which is worse than a
# map that is uniformly a little smaller.
MAX_INTERMEDIATE_PIXELS = 48_000_000
# And a hard stop on either side, so a map a hundred thousand units long does
# not become one pixel tall and forty thousand wide.
MAX_RASTER_SIDE = 8_000

# The face both backends set the map in, and the name the PDF knows it by.
# ReportLab ships these two files; ``Vera`` is Bitstream Vera Sans, whose
# licence permits redistribution and which covers every accented letter a map
# written in Portuguese, Spanish, French or German will contain.
FONT_REGULAR = "Vera.ttf"
FONT_BOLD = "VeraBd.ttf"
PDF_FONT_REGULAR = "MapaSans"
PDF_FONT_BOLD = "MapaSans-Bold"
# What the PDF falls back to if those files are ever not where they should be.
# Base-14, always present, and limited to WinAnsi - which is why it is the
# fallback and not the choice.
PDF_FALLBACK_REGULAR = "Helvetica"
PDF_FALLBACK_BOLD = "Helvetica-Bold"

# How far a label may be shrunk to fit its box before shrinking stops helping.
# Past this the wrap estimate was wrong about more than a word, and type this
# small inside a box this size reads as a rendering fault.
MIN_FIT_RATIO = 0.72

JPEG_QUALITY = 92
PDF_MIME = "application/pdf"
PNG_MIME = "image/png"
JPEG_MIME = "image/jpeg"


class PictureError(RuntimeError):
    """The drawing could not be turned into the requested file."""


@dataclass(frozen=True, slots=True)
class Picture:
    """Bytes, plus the two things a response needs to hand them over."""

    data: bytes
    mimetype: str
    extension: str


# ── PDF ─────────────────────────────────────────────────────────────────────


def to_pdf(scene: Scene) -> Picture:
    """The map as a single vector page, sized to the drawing itself.

    One page rather than a grid of tiled A4s. A mind map read across a page
    break is not a mind map; a reader who needs paper prints this scaled to
    fit, and a reader who needs to look closer has real vector to zoom into.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas as pdf_canvas

    scale = min(1.0, MAX_PAGE_POINTS / scene.width, MAX_PAGE_POINTS / scene.height)
    width = scene.width * scale
    height = scene.height * scale

    buffer = io.BytesIO()
    page = pdf_canvas.Canvas(buffer, pagesize=(width, height))
    page.setTitle(scene.title)
    page.setCreator(PRODUCER)
    page.setProducer(PRODUCER)

    # The drawing is described top-down and PDF measures bottom-up. Rather
    # than flipping the axis - which would flip every glyph with it - the
    # transform is folded into one function every point goes through.
    def place(x: float, y: float) -> Point:
        return ((x - scene.x) * scale, height - (y - scene.y) * scale)

    page.setFillColorRGB(*_unit_rgb(scene.paper))
    page.rect(0, 0, width, height, stroke=0, fill=1)

    if scene.message:
        _pdf_message(page, scene, place, scale)
    else:
        for connection in scene.connections:
            _pdf_connection(page, connection, scene.paper, place, scale)
        for card in scene.cards:
            _pdf_card(page, card, place, scale, pdfmetrics.stringWidth)

    page.showPage()
    page.save()
    return Picture(buffer.getvalue(), PDF_MIME, ".pdf")


def _pdf_message(page: Canvas, scene: Scene, place, scale: float) -> None:
    x, y = place(scene.x + scene.width / 2, scene.y + scene.height / 2 + 4)
    page.setFillColorRGB(*_unit_rgb(INK_MUTED))
    page.setFont(_pdf_face(strong=False), 15 * scale)
    page.drawCentredString(x, y, scene.message)


def _pdf_connection(
    page: Canvas, connection: Connection, paper: str, place, scale: float
) -> None:
    page.saveState()
    page.setStrokeColorRGB(*_unit_rgb(_faded(connection.colour, paper)))
    page.setLineWidth(CONNECTION_WIDTH * scale)
    page.setLineCap(1)  # round, as the SVG group asks for
    if connection.shared:
        page.setDash([DASH_ON * scale, DASH_OFF * scale], 0)

    path = page.beginPath()
    cursor: Point = (0.0, 0.0)
    for segment in connection.segments:
        if isinstance(segment, MoveTo):
            cursor = (segment.x, segment.y)
            path.moveTo(*place(*cursor))
        elif isinstance(segment, LineTo):
            cursor = (segment.x, segment.y)
            path.lineTo(*place(*cursor))
        else:
            first, second, end = _as_cubic(segment, cursor)
            path.curveTo(*place(*first), *place(*second), *place(*end))
            cursor = end
    page.drawPath(path, stroke=1, fill=0)
    page.restoreState()


def _pdf_card(page: Canvas, card: Card, place, scale: float, string_width) -> None:
    # roundRect is anchored bottom-left, so the corner handed to it is the
    # box's *bottom* edge once the page has been flipped.
    x, y = place(card.x, card.y + card.height)
    page.setFillColorRGB(*_unit_rgb(card.fill))
    page.setStrokeColorRGB(*_unit_rgb(card.stroke))
    page.setLineWidth(CARD_OUTLINE_WIDTH * scale)
    page.roundRect(
        x,
        y,
        card.width * scale,
        card.height * scale,
        _corner(card) * scale,
        stroke=1,
        fill=1,
    )

    if card.lines:
        face = _pdf_face(card.strong)
        size = _fitted_size(card, lambda text, at: string_width(text, face, at))
        page.setFillColorRGB(*_unit_rgb(card.text_colour))
        page.setFont(face, size * scale)
        for index, line in enumerate(card.lines):
            text_x, text_y = place(
                card.centre_x, card.first_baseline + index * LINE_HEIGHT
            )
            page.drawCentredString(text_x, text_y, line)

    if card.flagged:
        dot_x, dot_y = place(card.x + card.width - MARKER_INSET, card.y + MARKER_INSET)
        page.setFillColorRGB(*_unit_rgb(LINK))
        page.circle(dot_x, dot_y, MARKER_RADIUS * scale, stroke=0, fill=1)


# ── PNG and JPEG ────────────────────────────────────────────────────────────


def to_png(scene: Scene) -> Picture:
    buffer = io.BytesIO()
    _paint(scene).save(buffer, format="PNG", optimize=True)
    return Picture(buffer.getvalue(), PNG_MIME, ".png")


def to_jpeg(scene: Scene) -> Picture:
    buffer = io.BytesIO()
    # No transparency to lose: the drawing is ink on opaque paper by design,
    # which is also what makes JPEG an honest choice for it at all.
    _paint(scene).save(
        buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, subsampling=0
    )
    return Picture(buffer.getvalue(), JPEG_MIME, ".jpg")


def raster_scale(scene: Scene) -> float:
    """How many device pixels one scene unit gets, the budget applied."""
    scale = min(
        RASTER_SCALE,
        MAX_RASTER_SIDE / scene.width,
        MAX_RASTER_SIDE / scene.height,
    )
    # Measured on the buffer that is actually painted, which is the finished
    # size times the supersampling factor on both axes.
    buffer_area = scene.width * scene.height * SUPERSAMPLE * SUPERSAMPLE
    if buffer_area * scale * scale > MAX_INTERMEDIATE_PIXELS:
        scale = (MAX_INTERMEDIATE_PIXELS / buffer_area) ** 0.5
    return scale


def _paint(scene: Scene) -> Image.Image:
    scale = raster_scale(scene)
    width = max(int(round(scene.width * scale)), 1)
    height = max(int(round(scene.height * scale)), 1)
    factor = SUPERSAMPLE

    canvas = Image.new("RGB", (width * factor, height * factor), rgb_of(scene.paper))
    pen = ImageDraw.Draw(canvas)
    unit = scale * factor

    def place(x: float, y: float) -> Point:
        return ((x - scene.x) * unit, (y - scene.y) * unit)

    if scene.message:
        _write(
            pen,
            place(scene.x + scene.width / 2, scene.y + scene.height / 2 + 4),
            scene.message,
            _font(15.0 * unit),
            INK_MUTED,
        )
    else:
        for connection in scene.connections:
            _raster_connection(pen, connection, scene.paper, place, unit)
        for card in scene.cards:
            _raster_card(pen, card, place, unit)

    return canvas.resize((width, height), Image.Resampling.LANCZOS)


def _raster_connection(
    pen: ImageDraw.ImageDraw, connection: Connection, paper: str, place, unit: float
) -> None:
    points = [place(x, y) for x, y in _flatten(connection.segments, unit)]
    if len(points) < 2:
        return
    colour = rgb_of(_faded(connection.colour, paper))
    width = max(int(round(CONNECTION_WIDTH * unit)), 1)

    for run in _dashed(points, unit) if connection.shared else [points]:
        if len(run) >= 2:
            # ``joint="curve"`` rounds the corners between the straight pieces
            # a flattened Bezier is made of; without it a thick curve is a
            # chain of visible facets.
            pen.line(run, fill=colour, width=width, joint="curve")


def _raster_card(pen: ImageDraw.ImageDraw, card: Card, place, unit: float) -> None:
    left, top = place(card.x, card.y)
    right, bottom = place(card.x + card.width, card.y + card.height)
    pen.rounded_rectangle(
        [left, top, right, bottom],
        radius=_corner(card) * unit,
        fill=rgb_of(card.fill),
        outline=rgb_of(card.stroke),
        width=max(int(round(CARD_OUTLINE_WIDTH * unit)), 1),
    )

    if card.lines:
        def measure(text: str, at: float) -> float:
            """A line's width in scene units, as this face actually sets it."""
            return _font(at * unit, card.strong).getlength(text) / unit

        font = _font(_fitted_size(card, measure) * unit, card.strong)
        for index, line in enumerate(card.lines):
            _write(
                pen,
                place(card.centre_x, card.first_baseline + index * LINE_HEIGHT),
                line,
                font,
                card.text_colour,
            )

    if card.flagged:
        dot_x, dot_y = place(card.x + card.width - MARKER_INSET, card.y + MARKER_INSET)
        reach = MARKER_RADIUS * unit
        pen.ellipse(
            [dot_x - reach, dot_y - reach, dot_x + reach, dot_y + reach],
            fill=rgb_of(LINK),
        )


def _write(
    pen: ImageDraw.ImageDraw,
    anchor: Point,
    line: str,
    font: ImageFont.FreeTypeFont,
    colour: str,
) -> None:
    """One line of a label, sitting on its baseline.

    Drawn a line at a time on purpose. Handing Pillow the whole block would
    hand it the line pitch too, and its pitch comes from the font rather than
    from ``LINE_HEIGHT`` - the label would then break in the same places as
    the SVG and sit at different heights inside the box.
    """
    pen.multiline_text(anchor, line, font=font, fill=colour, anchor="ms")


# ── The face ────────────────────────────────────────────────────────────────


def _face_path(strong: bool) -> Path | None:
    """Where ReportLab keeps the face, or ``None`` if the install is unusual.

    Resolved through the installed package rather than hard-coded, and checked
    rather than assumed: a trimmed-down deployment that dropped the data files
    should degrade to a plainer picture, not to a 500.
    """
    import reportlab

    candidate = (
        Path(reportlab.__file__).parent
        / "fonts"
        / (FONT_BOLD if strong else FONT_REGULAR)
    )
    return candidate if candidate.is_file() else None


def _font(size: float, strong: bool = False) -> ImageFont.FreeTypeFont:
    """The face at a given size, rounded to whole pixels.

    The rounding is what makes the cache below hit: two boxes asking for
    27.9998 and 28.0001 pixels want the same font, and a map is a few hundred
    boxes - opening the file once per box is the difference between an export
    that feels instant and one that does not.
    """
    return _font_at(max(int(round(size)), 1), strong)


@lru_cache(maxsize=64)
def _font_at(pixels: int, strong: bool) -> ImageFont.FreeTypeFont:
    path = _face_path(strong)
    if path is None:
        logger.warning("Fonte do desenho ausente; usando a face padrão do Pillow.")
        return ImageFont.load_default(size=pixels)
    return ImageFont.truetype(str(path), pixels)


@lru_cache(maxsize=2)
def _pdf_face(strong: bool) -> str:
    """The PDF's name for the same face, registered on first use.

    A TrueType face rather than base-14 Helvetica, and not only for the sake
    of matching the bitmap: base-14 is WinAnsi, so a map written in Polish or
    Turkish would reach the page as mangled bytes. An embedded TrueType font
    carries whatever the writer actually typed.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # O arquivo primeiro, o registro depois. Ao contrário, uma instalação sem
    # os arquivos ainda devolveria o nome registrado por outro processo desta
    # mesma sessão - um nome que aponta para uma face que não está lá.
    path = _face_path(strong)
    if path is None:
        logger.warning("Fonte do desenho ausente; o PDF sai em Helvetica.")
        return PDF_FALLBACK_BOLD if strong else PDF_FALLBACK_REGULAR

    name = PDF_FONT_BOLD if strong else PDF_FONT_REGULAR
    # O registro do ReportLab é global ao processo, e registrar duas vezes o
    # mesmo nome é um aviso que ninguém precisa ler.
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


def _fitted_size(card: Card, measure) -> float:
    """The type size at which this label stays inside this box.

    ``measure(text, size)`` answers how wide a line is in scene units, which
    is the one thing the two backends cannot share - Pillow measures a bitmap
    face, ReportLab measures a PDF one, and they disagree by a fraction of a
    percent. Everything else about the decision is the same in both.
    """
    inner = card.width - PADDING_X * 2
    if inner <= 0 or not card.lines:
        return FONT_SIZE
    widest = max(measure(line, FONT_SIZE) for line in card.lines)
    if widest <= inner:
        return FONT_SIZE
    return FONT_SIZE * max(inner / widest, MIN_FIT_RATIO)


# ── Geometry the backends share ─────────────────────────────────────────────


def _corner(card: Card) -> float:
    """A radius that cannot exceed the box it is rounding.

    Past half the shorter side a corner radius is not rounder, it is
    malformed - ReportLab draws it as a knot and Pillow raises.
    """
    return min(card.radius, card.width / 2, card.height / 2)


def _as_cubic(segment: Segment, start: Point) -> tuple[Point, Point, Point]:
    """A curve segment as a cubic, whichever kind it arrived as.

    Neither backend draws quadratics, and every quadratic has an exact cubic
    twin: each control point sits two thirds of the way from its own end
    towards the single one. Exact rather than approximated, so an elbow's
    rounded corner is the same corner in all three formats.
    """
    if isinstance(segment, CurveTo):
        return (
            (segment.x1, segment.y1),
            (segment.x2, segment.y2),
            (segment.x, segment.y),
        )
    if not isinstance(segment, QuadTo):  # pragma: no cover - defensive
        raise PictureError("Segmento de curva desconhecido.")

    end = (segment.x, segment.y)
    control = (segment.x1, segment.y1)
    return (
        _along(start, control, 2.0 / 3.0),
        _along(end, control, 2.0 / 3.0),
        end,
    )


def _flatten(segments: tuple[Segment, ...], unit: float) -> list[Point]:
    """A path as the polyline a bitmap can actually draw.

    Each curve is cut into enough pieces that no piece spans more than a couple
    of device pixels, estimated from the control polygon - which is never
    shorter than the curve inside it, so the estimate errs towards more pieces
    rather than fewer.
    """
    points: list[Point] = []
    cursor: Point = (0.0, 0.0)
    for segment in segments:
        if isinstance(segment, MoveTo):
            cursor = (segment.x, segment.y)
            points.append(cursor)
            continue
        if isinstance(segment, LineTo):
            cursor = (segment.x, segment.y)
            points.append(cursor)
            continue

        first, second, end = _as_cubic(segment, cursor)
        span = (
            _distance(cursor, first) + _distance(first, second) + _distance(second, end)
        )
        steps = min(max(int(span * unit / 3.0), 8), 96)
        for step in range(1, steps + 1):
            points.append(_cubic_at(cursor, first, second, end, step / steps))
        cursor = end
    return points


def _cubic_at(start: Point, first: Point, second: Point, end: Point, t: float) -> Point:
    rest = 1.0 - t
    a = rest * rest * rest
    b = 3 * rest * rest * t
    c = 3 * rest * t * t
    d = t * t * t
    return (
        a * start[0] + b * first[0] + c * second[0] + d * end[0],
        a * start[1] + b * first[1] + c * second[1] + d * end[1],
    )


def _distance(a: Point, b: Point) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _along(start: Point, end: Point, t: float) -> Point:
    return (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)


def _dashed(points: list[Point], unit: float) -> list[list[Point]]:
    """Cut a polyline into the runs a dashed stroke actually paints.

    Pillow has no dash pattern, and the dash is not decoration here: it is the
    only thing telling the second path to a shared topic apart from the branch
    the topic really hangs from.
    """
    on = max(DASH_ON * unit, 1.0)
    off = max(DASH_OFF * unit, 1.0)
    runs: list[list[Point]] = []
    current: list[Point] = [points[0]]
    drawing = True
    remaining = on

    for index in range(1, len(points)):
        start = points[index - 1]
        end = points[index]
        length = _distance(start, end)
        if length <= 0.0:
            continue
        travelled = 0.0
        while length - travelled > remaining:
            travelled += remaining
            cut = _along(start, end, travelled / length)
            if drawing:
                current.append(cut)
                runs.append(current)
                current = []
            else:
                current = [cut]
            drawing = not drawing
            remaining = on if drawing else off
        remaining -= length - travelled
        if drawing:
            current.append(end)

    if drawing and len(current) >= 2:
        runs.append(current)
    return runs


# ── Colour ──────────────────────────────────────────────────────────────────


def _unit_rgb(colour: str) -> tuple[float, float, float]:
    """A hex literal as ReportLab's three 0-1 channels."""
    red, green, blue = rgb_of(colour)
    return (red / 255.0, green / 255.0, blue / 255.0)


def _faded(colour: str, paper: str) -> str:
    """A connection's colour, already mixed with the paper behind it.

    Both backends draw connections first, straight onto bare paper, so mixing
    here produces exactly the pixel a 55% stroke would have produced - without
    either backend needing an alpha channel it does not have.
    """
    mixed = tuple(
        round(ink * CONNECTION_OPACITY + behind * (1.0 - CONNECTION_OPACITY))
        for ink, behind in zip(rgb_of(colour), rgb_of(paper), strict=True)
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)
