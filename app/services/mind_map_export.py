"""Um mapa mental entrando e saindo em formatos que sobrevivem a este app.

Three conversions live here, and they are the reason a mind map in this
application is not a dead end:

``to_markdown``
    The map as a nested list. It is the format the rest of this product speaks,
    it diffs, it pastes anywhere, and it is what makes "exportar" mean the
    content rather than a picture of the content.
``to_svg``
    The map as a drawing, rendered on the server from the same coordinates the
    canvas uses. Vector rather than raster on purpose: it prints, it scales,
    and it needs no browser to produce - which is what makes it testable.
``outline_to_nodes`` / ``from_markdown``
    The way back in. A document with headings is already an outline; turning
    one into a map takes a parser, not a person.

Nothing here touches the request. The SVG is a string, the Markdown is a
string, and both are built from a graph the caller already loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from xml.sax.saxutils import quoteattr

from app.models import MindMap, MindMapNode
from app.models.mind_map import LAYOUTS
from app.services.mind_map_layout import (
    bounding_box,
    box_of,
    branch_path,
    branch_routing,
)
from app.services.sanitizer import MEDIA_URL_PREFIX

# ── Drawing constants ───────────────────────────────────────────────────────
# The exported drawing is a light document: it is going into a slide, a print
# or an email, none of which want a dark rectangle. The palette is therefore
# fixed rather than themed - a screenshot follows the app, a document follows
# the page it lands on.
INK = "#111827"
INK_MUTED = "#64748B"
PAPER = "#FFFFFF"
STROKE = "#CBD5E1"
LINK = "#4F46E5"

FONT_STACK = "Segoe UI, -apple-system, Helvetica Neue, Arial, sans-serif"
FONT_SIZE = 14.0
LINE_HEIGHT = 18.0
# Average advance width of the stack above at 14px. Wrapping is estimated, not
# measured - there is no font metric on the server - so the estimate is
# deliberately generous and the box is drawn at the size the browser reported.
CHAR_WIDTH = 7.3
PADDING_X = 14.0

MAX_OUTLINE_DEPTH = 12
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(?:\[[ xX]\]\s+)?(.+)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((<[^>]*>|[^)\s]+)\)")


# ── Markdown out ────────────────────────────────────────────────────────────


def to_markdown(mind_map: MindMap, nodes: list[MindMapNode]) -> str:
    """The map as a Markdown outline.

    The hierarchy becomes nesting, which is the one thing a list and a mind map
    genuinely share. Everything a node carries beyond its label - a note, a
    link, a picture - is written underneath it rather than crammed into the
    bullet, so the result reads as prose and still round-trips as structure.
    """
    tree = _index(nodes)
    lines: list[str] = [f"# {mind_map.title}", ""]
    if mind_map.description:
        lines.extend([mind_map.description, ""])

    for root in tree.roots:
        _write_branch(lines, tree, root, depth=0)

    # A single trailing newline: every other exporter in this app ends that way.
    return "\n".join(lines).rstrip() + "\n"


def _write_branch(lines: list[str], tree: _Index, node: MindMapNode, depth: int) -> None:
    indent = "  " * depth

    # Um tópico compartilhado sai uma vez, onde ele mora, e as outras
    # aparições apontam para ele. Repetir o ramo inteiro em cada etapa daria
    # um arquivo que se contradiz sozinho no dia em que alguém editar uma das
    # cópias - e o Markdown perderia justamente a informação que o espelho
    # existe para carregar: é o mesmo assunto.
    if node.mirror_of is not None:
        name = node.mirror_of.text.strip() or "(sem título)"
        lines.append(f"{indent}- {_escape_markdown(name)} *(o mesmo tópico, ver acima)*")
        return

    label = node.text.strip() or "(sem título)"

    if node.url:
        label = f"[{_escape_markdown(label)}]({_link_target(node.url)})"
    else:
        label = _escape_markdown(label)

    lines.append(f"{indent}- {label}")

    if node.kind == "image":
        source = _image_source(node)
        if source:
            lines.append(
                f"{indent}  ![{_escape_markdown(node.text.strip())}]"
                f"({_link_target(source)})"
            )

    if node.document is not None and not node.document.is_deleted:
        # A title carrying "]]" would end the wikilink early and leave the rest
        # of it loose in the outline.
        title = node.document.title.replace("]", "")
        lines.append(f"{indent}  Documento: [[{title}]]")

    if node.note:
        # Escaped like every other value the writer typed. Left raw, a note
        # beginning with "- " or "#" stopped being a note and became structure
        # the moment the file was read back.
        for line in node.note.splitlines():
            lines.append(f"{indent}  {_escape_markdown(line)}" if line.strip() else "")

    for child in tree.children.get(node.id, []):
        _write_branch(lines, tree, child, depth + 1)


def _image_source(node: MindMapNode) -> str:
    if node.media_asset is not None:
        # Relative on purpose: the export has to keep working behind whatever
        # host or port this installation is served on. The prefix comes from
        # the sanitizer, which is what decides that this is the one path
        # uploaded media is ever served from.
        return f"{MEDIA_URL_PREFIX}{node.media_asset.uuid}"
    return node.image_url


def _link_target(url: str) -> str:
    """A URL as it can appear inside ``(...)``.

    Angle brackets are the form Markdown defines for an address carrying
    characters that would otherwise end the link early - a parenthesis, a
    space. Applied only where it is needed: wrapping every URL would be noise
    on the ninety-nine that do not, and the reader of an exported outline is a
    person before it is a parser.
    """
    if any(char in url for char in "()<> "):
        return f"<{url}>"
    return url


def _escape_markdown(value: str) -> str:
    """Keep a value from turning into syntax when it lands in an outline.

    The backslash goes first, and it has to: escaping it after the others
    would double every escape this function had just added. ``#``, ``>``,
    ``+`` and ``-`` matter only at the start of a line - which is exactly
    where a note's own lines land when they are written under a bullet.
    """
    escaped = value.replace("\\", "\\\\")
    escaped = re.sub(r"([\[\]`*_])", r"\\\1", escaped)
    return re.sub(r"^(\s*)([#>+-])", r"\1\\\2", escaped)


# ── SVG out ─────────────────────────────────────────────────────────────────


def to_svg(mind_map: MindMap, nodes: list[MindMapNode]) -> str:
    """Draw the map exactly where the canvas has it.

    Built by hand rather than with a templating engine because every value that
    reaches it is escaped here, at the one boundary that knows it is producing
    XML. An SVG is a document a browser will happily execute, so nothing
    user-written is ever emitted unescaped.
    """
    if not nodes:
        return _empty_svg(mind_map)

    box = bounding_box(
        [(n.x, n.y, n.width, n.height) for n in nodes if n.mirror_of_id is None],
        padding=56.0,
    )
    tree = _index(nodes)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{box.width:.0f}" '
        f'height="{box.height:.0f}" '
        f'viewBox="{box.min_x:.1f} {box.min_y:.1f} {box.width:.1f} {box.height:.1f}" '
        f"role=\"img\" aria-label={quoteattr(mind_map.title)}>",
        f'<title>{escape(mind_map.title)}</title>',
        f'<rect x="{box.min_x:.1f}" y="{box.min_y:.1f}" width="{box.width:.1f}" '
        f'height="{box.height:.1f}" fill="{PAPER}"/>',
        ]

    # Connections first, so a line never crosses over the box it arrives at.
    parts.append('<g fill="none" stroke-linecap="round">')
    # The line follows the arrangement it belongs to, branch by branch: a tree
    # exported with the sideways curve of a horizontal map would be a picture
    # of a different map. The connection belongs to the *parent's*
    # arrangement, because the parent's arrangement is what decided where the
    # child went - even when the child opens a differently arranged branch of
    # its own below it.
    arrangement = _arrangements(mind_map, nodes, tree)
    for node in nodes:
        for child in tree.children.get(node.id, []):
            # Uma segunda aparição é uma linha, não uma caixa: ela leva do pai
            # até o tópico de verdade, desenhado noutro lugar da figura.
            target = _shown(child)
            parts.append(
                _branch_path(
                    node, target, mind_map.color,
                    branch_routing(arrangement[node.id]),
                    shared=target is not child,
                )
            )
    parts.append("</g>")

    for node in nodes:
        if node.mirror_of_id is not None:
            continue
        parts.append(_node_shape(node, mind_map.color))

    parts.append("</svg>")
    return "\n".join(parts)


def _empty_svg(mind_map: MindMap) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="200" '
        'viewBox="0 0 480 200" role="img" '
        f"aria-label={quoteattr(mind_map.title)}>"
        f'<rect width="480" height="200" fill="{PAPER}"/>'
        f'<text x="240" y="104" text-anchor="middle" font-family="{FONT_STACK}" '
        f'font-size="15" fill="{INK_MUTED}">Mapa sem tópicos</text>'
        "</svg>"
    )


def _arrangements(
    mind_map: MindMap, nodes: list[MindMapNode], tree: "_Index"
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


def _branch_path(
    parent: MindMapNode, child: MindMapNode, accent: str, routing: str,
    shared: bool = False,
) -> str:
    """The line from a parent to a child, drawn the way the layout draws it.

    The geometry itself belongs to :mod:`app.services.mind_map_layout`, which
    is also where the canvas gets it from - an exported map and the board it
    was exported from are then the same drawing rather than two drawings that
    agree most of the time.
    """
    path = branch_path(routing, box_of(parent), box_of(child))
    stroke = _colour(child.color, accent)
    # Tracejada quando é o segundo caminho até um tópico: a figura precisa
    # dizer isso sem legenda, como a tela diz.
    dash = ' stroke-dasharray="6 5"' if shared else ""
    return (
        f'<path d="{path}" stroke="{stroke}" stroke-width="2" '
        f'opacity="0.55"{dash}/>'
    )


def _shown(node: MindMapNode) -> MindMapNode:
    """O tópico que este nó mostra: ele mesmo, ou o original que ele espelha.

    A figura exportada e o quadro na tela precisam dizer a mesma coisa, e na
    tela um espelho mostra o texto de lá.
    """
    return node.mirror_of if node.mirror_of is not None else node


def _node_shape(node: MindMapNode, accent: str) -> str:
    # A caixa é deste nó - é aqui que ele está desenhado - mas o que se lê
    # dentro dela é do tópico que ele mostra.
    shown = _shown(node)
    fill = _colour(shown.color, accent if node.parent_id is None else PAPER)
    on_accent = node.parent_id is None or bool(shown.color)
    text_fill = "#FFFFFF" if on_accent else INK
    radius = {
        "pill": node.height / 2,
        "rect": 2.0,
        "ellipse": node.height / 2,
        "diamond": 4.0,
    }.get(node.shape, 12.0)

    body = (
        f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
        f'height="{node.height:.1f}" rx="{radius:.1f}" fill="{fill}" '
        f'stroke="{STROKE}" stroke-width="1"/>'
    )

    label = shown.text.strip()
    if not label:
        return body

    lines = _wrap(label, node.width)
    start_y = node.y + node.height / 2 - (len(lines) - 1) * LINE_HEIGHT / 2 + 5
    spans = "".join(
        f'<tspan x="{node.x + node.width / 2:.1f}" '
        f'y="{start_y + index * LINE_HEIGHT:.1f}">{escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    weight = "600" if node.parent_id is None else "500"
    text = (
        f'<text font-family="{FONT_STACK}" font-size="{FONT_SIZE}" '
        f'font-weight="{weight}" fill="{text_fill}" text-anchor="middle">{spans}</text>'
    )

    marker = ""
    if node.url:
        marker = (
            f'<circle cx="{node.x + node.width - 10:.1f}" cy="{node.y + 10:.1f}" '
            f'r="3.5" fill="{LINK}"/>'
        )
    return body + text + marker


def _wrap(value: str, width: float) -> list[str]:
    """Break a label to fit a box, estimating from average character width."""
    capacity = max(int((width - PADDING_X * 2) / CHAR_WIDTH), 6)
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
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
    limit = 4
    if len(lines) > limit:
        lines = [*lines[: limit - 1], lines[limit - 1][: capacity - 1] + "…"]
    return lines


def _colour(value: str, fallback: str) -> str:
    """Only a hex literal reaches the drawing.

    The service already refuses anything else on the way in; this is the second
    gate, because the value is about to be written into an attribute of a
    document format that executes.
    """
    candidate = (value or "").strip()
    if len(candidate) in {4, 7} and candidate.startswith("#"):
        try:
            int(candidate[1:], 16)
        except ValueError:
            return fallback
        return candidate
    return fallback


# ── Markdown in ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class OutlineItem:
    """One line of a parsed outline: a label, a depth and an optional link."""

    text: str
    depth: int
    url: str = ""


def parse_outline(markdown_text: str, limit: int = 400) -> list[OutlineItem]:
    """Read headings and bullets out of Markdown as a single nested outline.

    Headings set the level; bullets nest underneath the heading they follow,
    at their own indentation. Everything else is prose and is skipped - a mind
    map made of a document's paragraphs would be a wall of text on a canvas,
    which is what the document already was.
    """
    items: list[OutlineItem] = []
    heading_depth = 0

    for raw in (markdown_text or "").splitlines():
        if len(items) >= limit:
            break

        heading = _HEADING_RE.match(raw.strip())
        if heading:
            heading_depth = len(heading.group(1)) - 1
            label, url = _split_link(heading.group(2))
            items.append(
                OutlineItem(text=label, depth=min(heading_depth, MAX_OUTLINE_DEPTH), url=url)
            )
            continue

        bullet = _BULLET_RE.match(raw.rstrip())
        if bullet:
            indent = len(bullet.group(1).replace("\t", "  ")) // 2
            label, url = _split_link(bullet.group(2))
            if label:
                items.append(
                    OutlineItem(
                        text=label,
                        depth=min(heading_depth + 1 + indent, MAX_OUTLINE_DEPTH),
                        url=url,
                    )
                )

    return items


def _split_link(value: str) -> tuple[str, str]:
    """``[rótulo](url)`` becomes a label and a link; anything else stays text."""
    stripped = value.strip()
    match = _LINK_RE.fullmatch(stripped)
    if match:
        # Unwrapped here because _link_target wraps on the way out: a link the
        # exporter had to bracket has to survive being read back.
        url = match.group(2).strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        return match.group(1).strip(), url if url.startswith(("http://", "https://")) else ""
    return _LINK_RE.sub(r"\1", stripped), ""


# ── Shared indexing ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Index:
    roots: list[MindMapNode] = field(default_factory=list)
    children: dict[int, list[MindMapNode]] = field(default_factory=dict)


def _index(nodes: list[MindMapNode]) -> _Index:
    """Group a flat node list into roots and ordered child lists.

    Built once per export instead of walking ``node.children``, which would be
    a query per node on a map the caller already loaded whole.
    """
    known = {node.id for node in nodes}
    index = _Index()
    for node in sorted(nodes, key=lambda item: (item.position, item.id)):
        if node.parent_id in known:
            index.children.setdefault(node.parent_id, []).append(node)
        else:
            index.roots.append(node)
    return index
