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

    What to draw is not decided here. It comes from
    :func:`app.services.mind_map_drawing.build_scene`, which is also where the
    PDF and the bitmap get it from, so the four pictures of one map cannot
    disagree about where a box sits or how a label broke.
``outline_to_nodes`` / ``from_markdown``
    The way back in. A document with headings is already an outline; turning
    one into a map takes a parser, not a person.

Nothing here touches the request. The SVG is a string, the Markdown is a
string, and both are built from a graph the caller already loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from xml.sax.saxutils import quoteattr

from app.models import MindMap, MindMapNode
from app.services.mind_map_drawing import (
    FONT_SIZE,
    INK_MUTED,
    LINE_HEIGHT,
    LINK,
    MARKER_INSET,
    MARKER_RADIUS,
    Card,
    Connection,
    NodeIndex,
    Scene,
    build_scene,
    index_nodes,
)
from app.services.mind_map_layout import segments_to_path
from app.services.sanitizer import MEDIA_URL_PREFIX

# The one drawing constant that is genuinely about SVG and not about the
# drawing: which faces a viewer should try before falling back. Everything
# else - the ink, the metrics, the wrap - lives in ``mind_map_drawing``,
# because the PDF and the bitmap have to agree with this file about all of it.
FONT_STACK = "Segoe UI, -apple-system, Helvetica Neue, Arial, sans-serif"

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
    tree = index_nodes(nodes)
    lines: list[str] = [f"# {mind_map.title}", ""]
    if mind_map.description:
        lines.extend([mind_map.description, ""])

    for root in tree.roots:
        _write_branch(lines, tree, root, depth=0)

    # A single trailing newline: every other exporter in this app ends that way.
    return "\n".join(lines).rstrip() + "\n"


def _write_branch(
    lines: list[str], tree: NodeIndex, node: MindMapNode, depth: int
) -> None:
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
    """The map as vector, drawn exactly where the canvas has it.

    The drawing itself is decided in :mod:`app.services.mind_map_drawing`; this
    is only the transcription of it into XML. Written by hand rather than with
    a templating engine because every value that reaches it is escaped here, at
    the one boundary that knows it is producing a document a browser will
    execute - nothing user-written is ever emitted unescaped.
    """
    return render_svg(build_scene(mind_map, nodes))


def render_svg(scene: Scene) -> str:
    """A :class:`Scene` as an SVG document."""
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{scene.width:.0f}" '
        f'height="{scene.height:.0f}" '
        f'viewBox="{scene.x:.1f} {scene.y:.1f} {scene.width:.1f} {scene.height:.1f}" '
        f"role=\"img\" aria-label={quoteattr(scene.title)}>",
        f'<title>{escape(scene.title)}</title>',
        f'<rect x="{scene.x:.1f}" y="{scene.y:.1f}" width="{scene.width:.1f}" '
        f'height="{scene.height:.1f}" fill="{scene.paper}"/>',
    ]

    if scene.message:
        parts.append(_svg_message(scene))
        parts.append("</svg>")
        return "\n".join(parts)

    # Connections first, so a line never crosses over the box it arrives at.
    parts.append('<g fill="none" stroke-linecap="round">')
    parts.extend(_svg_connection(connection) for connection in scene.connections)
    parts.append("</g>")

    parts.extend(_svg_card(card) for card in scene.cards)
    parts.append("</svg>")
    return "\n".join(parts)


def _svg_message(scene: Scene) -> str:
    return (
        f'<text x="{scene.x + scene.width / 2:.1f}" '
        f'y="{scene.y + scene.height / 2 + 4:.1f}" text-anchor="middle" '
        f'font-family="{FONT_STACK}" font-size="15" fill="{INK_MUTED}">'
        f"{escape(scene.message)}</text>"
    )


def _svg_connection(connection: Connection) -> str:
    # Tracejada quando é o segundo caminho até um tópico: a figura precisa
    # dizer isso sem legenda, como a tela diz.
    dash = ' stroke-dasharray="6 5"' if connection.shared else ""
    return (
        f'<path d="{segments_to_path(connection.segments)}" '
        f'stroke="{connection.colour}" stroke-width="2" opacity="0.55"{dash}/>'
    )


def _svg_card(card: Card) -> str:
    body = (
        f'<rect x="{card.x:.1f}" y="{card.y:.1f}" width="{card.width:.1f}" '
        f'height="{card.height:.1f}" rx="{card.radius:.1f}" fill="{card.fill}" '
        f'stroke="{card.stroke}" stroke-width="1"/>'
    )

    text = ""
    if card.lines:
        spans = "".join(
            f'<tspan x="{card.centre_x:.1f}" '
            f'y="{card.first_baseline + index * LINE_HEIGHT:.1f}">{escape(line)}</tspan>'
            for index, line in enumerate(card.lines)
        )
        text = (
            f'<text font-family="{FONT_STACK}" font-size="{FONT_SIZE}" '
            f'font-weight="{"600" if card.strong else "500"}" '
            f'fill="{card.text_colour}" text-anchor="middle">{spans}</text>'
        )

    marker = ""
    if card.flagged:
        marker = (
            f'<circle cx="{card.x + card.width - MARKER_INSET:.1f}" '
            f'cy="{card.y + MARKER_INSET:.1f}" r="{MARKER_RADIUS}" fill="{LINK}"/>'
        )
    return body + text + marker


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
