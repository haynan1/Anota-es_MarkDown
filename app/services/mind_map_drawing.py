"""Um mapa mental como desenho, antes de ser um arquivo.

Why this module exists
----------------------
A map now leaves this application as four things: an outline in Markdown, a
vector drawing in SVG, a page in PDF, and a picture in PNG or JPEG. The last
three are the *same drawing* - same frame, same boxes, same curves, same wrap
points - and the only honest way to keep them the same is to decide all of it
once, here, where no output format is in the room.

What comes out is a :class:`Scene`: absolute coordinates, resolved colours,
already-wrapped label lines and the segments of every connection. A backend
receives it and does nothing but transcribe - ``to_svg`` writes attributes,
``mind_map_picture`` moves a pen. That is what stops the PDF from wrapping a
label one word differently from the SVG, which is exactly the kind of drift
nobody notices until the two are put side by side in a slide.

The palette is fixed rather than themed
---------------------------------------
The board follows the application's theme; an export follows the page it lands
on. A slide, a print and an email all want dark ink on white paper, so that is
what every picture backend produces - a screenshot is the thing that follows
the app, and a screenshot is not what this is.

Nothing here touches the request, the database or a file. Rows in, geometry
out, which is what makes every promise in this file testable without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import MindMap, MindMapNode
from app.models.mind_map import LAYOUTS
from app.services.mind_map_layout import (
    Segment,
    bounding_box,
    box_of,
    branch_routing,
    branch_segments,
)

# ── Ink ─────────────────────────────────────────────────────────────────────

INK = "#111827"
INK_MUTED = "#64748B"
PAPER = "#FFFFFF"
STROKE = "#CBD5E1"
LINK = "#4F46E5"

FONT_SIZE = 14.0
LINE_HEIGHT = 18.0
# Average advance width of the export font at 14px. Wrapping is estimated, not
# measured - there is no font metric on the server, and the two picture
# backends do not even share one - so the estimate is deliberately generous
# and the box is drawn at the size the browser reported.
CHAR_WIDTH = 7.3
PADDING_X = 14.0
# How many lines fit inside a box the browser measured. Past this the label is
# cut with an ellipsis rather than allowed to spill over the edge.
MAX_LABEL_LINES = 4

# Air around the drawing. The same padding "enquadrar" uses on screen, so an
# exported map is framed the way it was framed when it was exported.
FRAME_PADDING = 56.0

# The placeholder a map with no topics becomes. It is drawn rather than
# refused: an empty export that opens and says so is a better answer than a
# download that fails.
EMPTY_WIDTH = 480.0
EMPTY_HEIGHT = 200.0
EMPTY_MESSAGE = "Mapa sem tópicos"

# The little dot marking a topic that carries a link. Small enough to read as
# a mark rather than as a shape somebody drew.
MARKER_RADIUS = 3.5
MARKER_INSET = 10.0

# Every shape a node can be, as the radius of the rounded rectangle that
# stands in for it. ``ellipse`` and ``diamond`` have no true outline in an
# export yet; they borrow the nearest rectangle rather than silently becoming
# a plain box, which is what the drawing has always done.
SHAPE_RADII = {"rect": 2.0, "diamond": 4.0}
DEFAULT_RADIUS = 12.0
# The two shapes whose corner radius is half their own height - a capsule.
FULLY_ROUNDED = frozenset({"pill", "ellipse"})


# ── The scene ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Connection:
    """One line between two topics, already routed."""

    segments: tuple[Segment, ...]
    colour: str
    #: A second path to a topic that lives elsewhere. The picture has to say
    #: so without a legend, exactly as the board does.
    shared: bool


@dataclass(frozen=True, slots=True)
class Card:
    """One topic, as a rectangle with words in it."""

    x: float
    y: float
    width: float
    height: float
    radius: float
    fill: str
    stroke: str
    lines: tuple[str, ...]
    text_colour: str
    #: The central idea is set heavier than the branches hanging off it.
    strong: bool
    #: Carries a link - drawn as a dot in the top-right corner.
    flagged: bool

    @property
    def centre_x(self) -> float:
        return self.x + self.width / 2

    @property
    def first_baseline(self) -> float:
        """Where the first line of the label sits.

        The block of lines is centred on the box and then nudged down by a
        third of the cap height, because a baseline is not the middle of a
        letter - text centred on its baseline reads as sitting slightly high.
        """
        block = (len(self.lines) - 1) * LINE_HEIGHT
        return self.y + self.height / 2 - block / 2 + 5.0


@dataclass(frozen=True, slots=True)
class Scene:
    """A whole map, framed and ready to be transcribed into any format."""

    title: str
    #: The frame, in board coordinates. Every backend subtracts this origin.
    x: float
    y: float
    width: float
    height: float
    paper: str = PAPER
    connections: tuple[Connection, ...] = ()
    cards: tuple[Card, ...] = ()
    #: The placeholder to draw instead of a map, set only when there is no map
    #: to draw. Every backend branches on it, and its emptiness is the whole
    #: signal - a separate ``is_empty`` would be a second way to ask one
    #: question, and the two would eventually answer differently.
    message: str = ""


# ── Building one ────────────────────────────────────────────────────────────


def build_scene(mind_map: MindMap, nodes: list[MindMapNode]) -> Scene:
    """The map as a drawing: frame, connections, boxes, words.

    Connections come first in the scene as they come first on the board, so a
    backend that draws the list in order never puts a curve over the box it
    arrives at.
    """
    if not nodes:
        return Scene(
            title=mind_map.title,
            x=0.0,
            y=0.0,
            width=EMPTY_WIDTH,
            height=EMPTY_HEIGHT,
            message=EMPTY_MESSAGE,
        )

    # Mirrors are lines, not boxes: a second appearance of a topic is drawn
    # where the original is, so it must not stretch the frame to a place where
    # nothing will be painted.
    frame = bounding_box(
        [(n.x, n.y, n.width, n.height) for n in nodes if n.mirror_of_id is None],
        padding=FRAME_PADDING,
    )
    tree = index_nodes(nodes)
    arrangement = effective_arrangements(mind_map, nodes, tree)
    centre = centre_of(tree)

    connections: list[Connection] = []
    for node in nodes:
        for child in tree.children.get(node.id, []):
            target = shown(child)
            connections.append(
                Connection(
                    segments=branch_segments(
                        branch_routing(arrangement[node.id]),
                        box_of(node),
                        box_of(target),
                    ),
                    colour=clean_colour(target.color, mind_map.color),
                    shared=target is not child,
                )
            )

    cards = tuple(
        _card(node, mind_map.color, is_centre=node is centre)
        for node in nodes
        if node.mirror_of_id is None
    )

    return Scene(
        title=mind_map.title,
        x=frame.min_x,
        y=frame.min_y,
        width=frame.width,
        height=frame.height,
        connections=tuple(connections),
        cards=cards,
    )


def _card(node: MindMapNode, accent: str, is_centre: bool) -> Card:
    # The box belongs to this node - this is where it is drawn - but what is
    # read inside it belongs to the topic it shows.
    label_source = shown(node)
    # Só o centro veste o acento. Todo tópico sem pai vestia, e um ramo que
    # alguém tinha acabado de desconectar aparecia na figura anunciando-se
    # como o assunto principal do mapa - enquanto na tela ele continuava um
    # tópico comum. A regra é a mesma dos dois lados agora.
    fill = clean_colour(label_source.color, accent if is_centre else PAPER)
    on_accent = is_centre or bool(label_source.color)

    if node.shape in FULLY_ROUNDED:
        radius = node.height / 2
    else:
        radius = SHAPE_RADII.get(node.shape, DEFAULT_RADIUS)

    label = label_source.text.strip()
    return Card(
        x=float(node.x),
        y=float(node.y),
        width=float(node.width),
        height=float(node.height),
        radius=float(radius),
        fill=fill,
        stroke=STROKE,
        lines=wrap_label(label, float(node.width)) if label else (),
        text_colour="#FFFFFF" if on_accent else INK,
        strong=is_centre,
        flagged=bool(node.url),
    )


# ── Shared reading ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class NodeIndex:
    """A map's rows arranged as the tree they describe.

    Built once and handed round, because every walk over a map - the outline,
    the drawing, the arrangement resolution - needs the same two answers: what
    are the roots, and what hangs off this row.
    """

    roots: list[MindMapNode] = field(default_factory=list)
    children: dict[int, list[MindMapNode]] = field(default_factory=dict)


def index_nodes(nodes: list[MindMapNode]) -> NodeIndex:
    """Group a flat node list into roots and ordered child lists.

    Built once per export instead of walking ``node.children``, which would be
    a query per node on a map the caller already loaded whole.

    A node whose parent is not in the list counts as a root. That is not
    defensive noise: it is what keeps a partial list - one branch, a filtered
    selection - from silently losing the rows whose parent was left behind.
    """
    known = {node.id for node in nodes}
    index = NodeIndex()
    for node in sorted(nodes, key=lambda item: (item.position, item.id)):
        if node.parent_id in known:
            index.children.setdefault(node.parent_id, []).append(node)
        else:
            index.roots.append(node)
    return index


def centre_of(tree: NodeIndex) -> MindMapNode | None:
    """O centro do mapa: o primeiro tópico sem pai, o que nasceu com ele.

    Ordenado pela mesma chave que a tela usa em ``store.roots()`` - posição, e
    o uuid para desempatar - porque as duas respostas têm de ser o mesmo nó. A
    tela ordena por uuid e as linhas deste índice por ``id``; num empate de
    posição os dois discordariam, e a figura coroaria um tópico que a tela
    não coroa.
    """
    if not tree.roots:
        return None
    return min(tree.roots, key=lambda node: (node.position, node.uuid))


def shown(node: MindMapNode) -> MindMapNode:
    """O tópico que este nó mostra: ele mesmo, ou o original que ele espelha.

    A figura exportada e o quadro na tela precisam dizer a mesma coisa, e na
    tela um espelho mostra o texto de lá.
    """
    return node.mirror_of if node.mirror_of is not None else node


def effective_arrangements(
    mind_map: MindMap, nodes: list[MindMapNode], tree: NodeIndex
) -> dict[int, str]:
    """What each node is arranged by, inheritance applied - keyed by row id.

    The same walk :func:`app.services.mind_map_layout.effective_layouts` does
    for the layout, over rows instead of layout nodes. Two walks of one rule
    is a drift risk worth naming: the tests pin them against each other, over
    the same tree, so a change to inheritance that reaches only one of them
    fails rather than producing an export drawn unlike its board.
    """
    resolved: dict[int, str] = {}
    stack: list[tuple[int, str]] = [(root.id, mind_map.layout) for root in tree.roots]
    by_id = {node.id: node for node in nodes}
    while stack:
        identifier, inherited = stack.pop()
        if identifier in resolved:
            continue
        node = by_id.get(identifier)
        own = node.layout if node is not None and node.layout in LAYOUTS else None
        mine = own or inherited
        resolved[identifier] = mine
        stack.extend((child.id, mine) for child in tree.children.get(identifier, []))

    for node in nodes:
        resolved.setdefault(node.id, mind_map.layout)
    return resolved


def wrap_label(value: str, width: float) -> tuple[str, ...]:
    """Break a label to fit a box, estimating from average character width."""
    capacity = max(int((width - PADDING_X * 2) / CHAR_WIDTH), 6)
    lines: list[str] = []
    current = ""
    for word in value.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= capacity or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    # A box has a height the browser measured; more lines than fit in it would
    # spill over the edge, so the overflow is marked rather than drawn.
    if len(lines) > MAX_LABEL_LINES:
        lines = [
            *lines[: MAX_LABEL_LINES - 1],
            lines[MAX_LABEL_LINES - 1][: capacity - 1] + "…",
        ]
    return tuple(lines)


def clean_colour(value: str, fallback: str) -> str:
    """Only a hex literal reaches a drawing.

    The service already refuses anything else on the way in; this is the second
    gate, because the value is about to be written into an attribute of a
    document format that executes - and, in the raster backend, parsed into
    three integers.
    """
    candidate = (value or "").strip()
    if len(candidate) in {4, 7} and candidate.startswith("#"):
        try:
            int(candidate[1:], 16)
        except ValueError:
            return fallback
        return candidate
    return fallback


def rgb_of(colour: str) -> tuple[int, int, int]:
    """A validated hex literal as three 0-255 channels.

    Both short (``#abc``) and long (``#aabbcc``) forms, because
    :func:`clean_colour` admits both and a backend must not have to know which
    one it was handed.
    """
    body = colour.lstrip("#")
    if len(body) == 3:
        body = "".join(char * 2 for char in body)
    return (int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16))
