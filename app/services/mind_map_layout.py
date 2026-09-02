"""Arrumação automática de um mapa mental.

Pure geometry: nodes in, coordinates out. No database, no Flask, no ORM - the
same reason ``chart_geometry`` exists. Everything here is arithmetic over a
tree, so every decision about spacing is unit-testable without a request.

Why the layout lives on the server
----------------------------------
The canvas could tidy itself in the browser, and then the exporter would need
its own copy of the same arithmetic to place nodes in an SVG - two
implementations of one truth, guaranteed to drift. Here it is computed once and
both the canvas and the exporter ask for it.

The algorithm is the classic tidy-tree pass: a post-order walk assigns each
leaf the next free slot along the cross axis, and each parent is centred on its
children. It is O(n), stable (the same map always lands in the same place) and
produces the arrangement people expect from a mind map - no crossings, no
overlaps, siblings in the order the writer put them.

A collapsed node is laid out as a leaf. Its descendants keep the coordinates
they had, out of sight behind it; expanding the branch and tidying again is
what places them. Reserving space for something nobody can see would leave a
hole in the middle of the map.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from math import cos, floor, hypot, inf, pi, sin

# Breathing room between a parent column and the next, and between siblings.
# Chosen so an arrow has room to curve without the branches touching.
GAP_MAIN = 96.0
GAP_CROSS = 28.0
# The tree grows downwards, so its axes carry the opposite loads: rows can sit
# closer than columns can, because a row is one node tall and a column is a
# whole branch wide - and siblings standing side by side need more air between
# them than siblings stacked do, or the shared bus under a parent turns into a
# smudge.
TREE_GAP_MAIN = 76.0
TREE_GAP_CROSS = 44.0
# Radial rings are wider: the same gap that reads as generous in a column is
# cramped once the nodes fan out around a circle.
RADIAL_RING = 260.0
RADIAL_MIN_ARC = 0.18

# Which layouts run down the page rather than across it. Named once, because
# three modules ask the question and a fourth answer would be a bug.
VERTICAL_LAYOUTS = frozenset({"down", "tree"})

# How the line from a parent to a child is drawn, per layout.
#
# The shape of the connection is not decoration on top of the layout; it is
# part of the same decision. A top-down map drawn with the sideways curve of a
# horizontal one reads as a map that grew the wrong way - branches leaving the
# left and right faces of a node whose children are underneath it. That is
# exactly the complaint that "só dá para a direita ou para a esquerda"
# describes, and it was true of every layout here that is not ``right``.
BRANCH_ROUTINGS = ("horizontal", "vertical", "elbow", "spoke")
BRANCH_ROUTING = {
    "right": "horizontal",
    "down": "vertical",
    "tree": "elbow",
    "radial": "spoke",
}

# The radius of an elbow's shoulder, and the smallest run of straight line
# worth rounding. Below it the corner is drawn square: a 2px arc on a 3px
# segment is a wobble, not a curve.
ELBOW_RADIUS = 14.0
# How far a curve leans out of the face it leaves, at minimum. Short links
# would otherwise be drawn as very flat S-shapes that read as straight lines
# arriving at the wrong angle.
CURVE_REACH = 24.0


def board_orientation(layout: str) -> str:
    """Which faces of a node a connection uses: ``horizontal``, ``vertical``
    or ``radial``.

    The board carries this as an attribute so the connection ports sit on the
    two sides the map actually grows along. Rendered by the server as well as
    written by the canvas, so the ports are right in the first painted frame
    rather than one frame later.
    """
    if layout == "radial":
        return "radial"
    return "vertical" if layout in VERTICAL_LAYOUTS else "horizontal"


def branch_routing(layout: str) -> str:
    """How ``layout`` draws a parent-child connection.

    An unknown name - a hand-edited row, an old map from before a layout was
    added - falls back to the horizontal curve rather than raising: a map that
    draws slightly wrong is recoverable, and a map that refuses to draw is not.
    """
    return BRANCH_ROUTING.get(layout, "horizontal")


@dataclass(slots=True)
class LayoutNode:
    """One node as the layout sees it: an identity, a parent and a size."""

    key: str
    parent: str | None
    width: float
    height: float
    collapsed: bool = False


@dataclass(slots=True)
class _Tree:
    roots: list[str]
    children: dict[str, list[str]] = field(default_factory=dict)


def build_tree(nodes: Sequence[LayoutNode]) -> _Tree:
    """Group the nodes into roots and child lists, tolerating a broken graph.

    A node whose parent is missing, or which sits in a cycle, is promoted to a
    root instead of vanishing. The service refuses to create either, but a
    layout pass is not the place to discover that a database was edited by
    hand - the map still has to draw.
    """
    known = {node.key for node in nodes}
    children: dict[str, list[str]] = {node.key: [] for node in nodes}
    roots: list[str] = []

    for node in nodes:
        if node.parent is not None and node.parent in known and node.parent != node.key:
            children[node.parent].append(node.key)
        else:
            roots.append(node.key)

    # Anything not reachable from a root is inside a cycle. Break it by
    # promoting the first node of each orphaned component.
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        key = stack.pop()
        if key in reachable:
            continue
        reachable.add(key)
        stack.extend(children.get(key, ()))

    for node in nodes:
        if node.key not in reachable:
            reachable.add(node.key)
            roots.append(node.key)
            parent = node.parent
            if parent in children and node.key in children[parent]:
                children[parent].remove(node.key)
            stack = list(children.get(node.key, ()))
            while stack:
                key = stack.pop()
                if key in reachable:
                    continue
                reachable.add(key)
                stack.extend(children.get(key, ()))

    return _Tree(roots=roots, children=children)


def compute_layout(
    nodes: Sequence[LayoutNode],
    direction: str = "right",
    origin: tuple[float, float] = (0.0, 0.0),
) -> dict[str, tuple[float, float]]:
    """Return ``{key: (x, y)}`` - the top-left corner of every node.

    ``direction`` is one of ``right`` (branches grow to the right, the classic
    mind map), ``down`` (the same map stood on end), ``tree`` (a top-down org
    chart, drawn with square shoulders) or ``radial`` (branches fan out around
    the root).

    ``down`` and ``tree`` share this arithmetic and differ in how the
    connection between a parent and a child is drawn - which is not a detail
    the layout hides from anyone: :func:`branch_routing` names the difference,
    and the canvas and the exporter both read it from here.
    """
    if not nodes:
        return {}
    if direction == "radial":
        return _radial(nodes, origin)
    return _orthogonal(nodes, direction, origin)


# ── Orthogonal (right / down / tree) ────────────────────────────────────────


def _orthogonal(
    nodes: Sequence[LayoutNode], direction: str, origin: tuple[float, float]
) -> dict[str, tuple[float, float]]:
    """One depth per column (or row); siblings stacked along the cross axis."""
    horizontal = direction not in VERTICAL_LAYOUTS
    gap_main = TREE_GAP_MAIN if direction == "tree" else GAP_MAIN
    gap_cross = TREE_GAP_CROSS if direction == "tree" else GAP_CROSS
    by_key = {node.key: node for node in nodes}
    tree = build_tree(nodes)

    depth = _depths(tree, by_key)
    # The main axis is laid out by level, so a level is as wide as its widest
    # node. Ragged columns are what make an auto-tidied map look hand-made in
    # the bad sense.
    extent: dict[int, float] = {}
    for key, level in depth.items():
        node = by_key[key]
        size = node.width if horizontal else node.height
        extent[level] = max(extent.get(level, 0.0), size)

    offsets: dict[int, float] = {}
    running = 0.0
    for level in sorted(extent):
        offsets[level] = running
        running += extent[level] + gap_main

    positions: dict[str, tuple[float, float]] = {}
    cursor = 0.0

    def place(key: str) -> tuple[float, float]:
        """Post-order: children first, then centre the parent on them."""
        nonlocal cursor
        node = by_key[key]
        cross_size = node.height if horizontal else node.width
        kids = [] if node.collapsed else tree.children.get(key, [])

        if not kids:
            start = cursor
            cursor = start + cross_size + gap_cross
            centre = start + cross_size / 2
        else:
            centres = [place(child) for child in kids]
            first, last = centres[0], centres[-1]
            centre = (first[0] + last[0]) / 2
            # A parent taller than the span of its children would spill out of
            # its own column, so the cursor never moves backwards.
            start = centre - cross_size / 2
            cursor = max(cursor, start + cross_size + gap_cross)

        main = offsets[depth[key]]
        cross = centre - cross_size / 2
        positions[key] = (
            (origin[0] + main, origin[1] + cross)
            if horizontal
            else (origin[0] + cross, origin[1] + main)
        )
        return (centre, cross_size)

    for root in tree.roots:
        place(root)
        # A visible gutter between two independent trees on the same board.
        cursor += gap_cross * 2

    return positions


def _depths(tree: _Tree, by_key: dict[str, LayoutNode]) -> dict[str, int]:
    """Breadth-first depth of every node. Iterative: a deep map is not a
    reason to hit the recursion limit."""
    depth: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(root, 0) for root in tree.roots]
    while queue:
        key, level = queue.pop()
        if key in depth:
            continue
        depth[key] = level
        if by_key[key].collapsed:
            continue
        queue.extend((child, level + 1) for child in tree.children.get(key, ()))

    # Anything a collapsed ancestor hid still needs a coordinate.
    for key in by_key:
        depth.setdefault(key, 0)
    return depth


# ── Radial ──────────────────────────────────────────────────────────────────


def _radial(
    nodes: Sequence[LayoutNode], origin: tuple[float, float]
) -> dict[str, tuple[float, float]]:
    """Branches fan around the root, each getting a slice of the circle
    proportional to how many leaves it carries."""
    by_key = {node.key: node for node in nodes}
    tree = build_tree(nodes)
    leaves = _leaf_counts(tree, by_key)

    positions: dict[str, tuple[float, float]] = {}
    centre_x, centre_y = origin

    def place(key: str, level: int, start: float, end: float) -> None:
        node = by_key[key]
        angle = (start + end) / 2
        radius = level * RADIAL_RING
        x = centre_x + radius * cos(angle) - node.width / 2
        y = centre_y + radius * sin(angle) - node.height / 2
        positions[key] = (x, y)

        kids = [] if node.collapsed else tree.children.get(key, [])
        if not kids:
            return

        total = sum(leaves[child] for child in kids) or 1
        # A branch never gets a slice too thin to read; the arc is widened at
        # the cost of the span, which only overlaps far from the centre.
        span = max(end - start, RADIAL_MIN_ARC * len(kids))
        cursor = angle - span / 2 if level == 0 else start
        if level == 0:
            cursor = -pi
            span = 2 * pi
        for child in kids:
            slice_size = span * (leaves[child] / total)
            place(child, level + 1, cursor, cursor + slice_size)
            cursor += slice_size

    for index, root in enumerate(tree.roots):
        # Extra roots are parked on their own circles to the right, rather than
        # sharing a centre and drawing on top of each other.
        root_origin = centre_x + index * RADIAL_RING * 4
        centre_x, saved = root_origin, centre_x
        place(root, 0, -pi, pi)
        centre_x = saved

    return positions


def _leaf_counts(tree: _Tree, by_key: dict[str, LayoutNode]) -> dict[str, int]:
    """How many leaves hang off each node - the weight of its slice."""
    counts: dict[str, int] = {}

    order = _post_order(tree, by_key)
    for key in order:
        kids = [] if by_key[key].collapsed else tree.children.get(key, [])
        counts[key] = sum(counts.get(child, 1) for child in kids) or 1
    for key in by_key:
        counts.setdefault(key, 1)
    return counts


def _post_order(tree: _Tree, by_key: dict[str, LayoutNode]) -> list[str]:
    """Children before parents, iteratively."""
    order: list[str] = []
    stack: list[tuple[str, bool]] = [(root, False) for root in reversed(tree.roots)]
    seen: set[str] = set()
    while stack:
        key, expanded = stack.pop()
        if expanded:
            order.append(key)
            continue
        if key in seen:
            continue
        seen.add(key)
        stack.append((key, True))
        kids = [] if by_key[key].collapsed else tree.children.get(key, [])
        stack.extend((child, False) for child in reversed(kids))
    return order


# ── Connections ─────────────────────────────────────────────────────────────
#
# The ``d`` of every line on the board, computed here so the canvas and the
# SVG export cannot disagree about where a branch goes. The browser has its
# own copy of these functions in ``static/js/modules/mindmap/routing.js`` - it
# has to, because a link is redrawn on every frame of a drag and a round trip
# per frame is not a thing anyone would ship. That is a duplication, so it is
# pinned: ``tests/js/mindmap-routing.test.mjs`` runs the same cases through
# both and compares the strings character for character.


@dataclass(frozen=True, slots=True)
class Box:
    """A node as a connection sees it: a rectangle, nothing else."""

    x: float
    y: float
    width: float
    height: float

    @property
    def centre_x(self) -> float:
        return self.x + self.width / 2

    @property
    def centre_y(self) -> float:
        return self.y + self.height / 2

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


def box_of(node: object) -> Box:
    """A :class:`Box` from anything carrying ``x``, ``y``, ``width`` and
    ``height`` - a model row, a dataclass, a stub in a test."""
    return Box(float(node.x), float(node.y), float(node.width), float(node.height))


def _n(value: float) -> str:
    """One decimal, rounded half away from zero, and never ``-0.0``.

    Python rounds halves to even and JavaScript's ``toFixed`` rounds them away
    from zero, so ``0.25`` would come out ``0.2`` on the server and ``0.3`` in
    the browser - one character of difference, on a value nobody could see,
    that would break the test holding the two implementations together. The
    rounding is therefore done here rather than left to the formatter.
    """
    rounded = floor(abs(value) * 10.0 + 0.5) / 10.0
    if value < 0:
        rounded = -rounded
    return f"{rounded + 0.0:.1f}"


def _sign(value: float) -> float:
    if value > 0:
        return 1.0
    return -1.0 if value < 0 else 0.0


def branch_path(routing: str, parent: Box, child: Box) -> str:
    """The ``d`` of the line from ``parent`` to ``child``."""
    if routing == "vertical":
        return _vertical_curve(parent, child)
    if routing == "elbow":
        return _elbow(parent, child)
    if routing == "spoke":
        return _spoke(parent, child)
    return _horizontal_curve(parent, child)


def _horizontal_curve(parent: Box, child: Box) -> str:
    """A cubic leaving the parent sideways and arriving the same way."""
    x1, x2 = parent.right, child.x
    if child.right < parent.x:
        # The child sits to the left; leave from that face instead of looping
        # the line back across the parent.
        x1, x2 = parent.x, child.right
    y1, y2 = parent.centre_y, child.centre_y

    reach = max(abs(x2 - x1) * 0.5, CURVE_REACH)
    lean = 1.0 if x2 >= x1 else -1.0
    return (
        f"M{_n(x1)},{_n(y1)} C{_n(x1 + reach * lean)},{_n(y1)} "
        f"{_n(x2 - reach * lean)},{_n(y2)} {_n(x2)},{_n(y2)}"
    )


def _vertical_curve(parent: Box, child: Box) -> str:
    """The same curve stood on end: out of the bottom face, into the top."""
    y1, y2 = parent.bottom, child.y
    if child.bottom < parent.y:
        y1, y2 = parent.y, child.bottom
    x1, x2 = parent.centre_x, child.centre_x

    reach = max(abs(y2 - y1) * 0.5, CURVE_REACH)
    lean = 1.0 if y2 >= y1 else -1.0
    return (
        f"M{_n(x1)},{_n(y1)} C{_n(x1)},{_n(y1 + reach * lean)} "
        f"{_n(x2)},{_n(y2 - reach * lean)} {_n(x2)},{_n(y2)}"
    )


def _elbow(parent: Box, child: Box) -> str:
    """Down, across, down again - the org-chart connector.

    The turn happens exactly halfway between the two rows, and that is what
    makes every child of one parent share a single horizontal run: a tidied
    tree puts a whole level on one line, so the midpoints coincide and the
    shoulders draw one bus rather than a fan of near-misses. Dragged out of
    alignment by hand, each connector turns at its own midpoint - the shape
    degrades into an honest elbow instead of into a knot.
    """
    y1, y2 = parent.bottom, child.y
    if child.bottom < parent.y:
        y1, y2 = parent.y, child.bottom
    x1, x2 = parent.centre_x, child.centre_x
    middle = (y1 + y2) / 2

    across = _sign(x2 - x1)
    radius = min(ELBOW_RADIUS, abs(x2 - x1) / 2, abs(middle - y1), abs(y2 - middle))
    if across == 0.0 or radius < 1.0:
        # Straight under the parent, or too little room to round the corner
        # honestly. Routing through the midpoint collapses to a plain vertical
        # line when the two centres agree - the common case for an only child,
        # and the one place a curve would look like a mistake.
        return (
            f"M{_n(x1)},{_n(y1)} L{_n(x1)},{_n(middle)} "
            f"L{_n(x2)},{_n(middle)} L{_n(x2)},{_n(y2)}"
        )

    first = _sign(middle - y1)
    second = _sign(y2 - middle)
    return (
        f"M{_n(x1)},{_n(y1)} "
        f"L{_n(x1)},{_n(middle - radius * first)} "
        f"Q{_n(x1)},{_n(middle)} {_n(x1 + radius * across)},{_n(middle)} "
        f"L{_n(x2 - radius * across)},{_n(middle)} "
        f"Q{_n(x2)},{_n(middle)} {_n(x2)},{_n(middle + radius * second)} "
        f"L{_n(x2)},{_n(y2)}"
    )


def _spoke(parent: Box, child: Box) -> str:
    """A straight run between two boxes, cut at the faces it crosses.

    Radial is the one layout where a child can sit in any direction at all
    from its parent, so the only honest answer is the line between the two
    centres. It is drawn straight on purpose: in a fan the angle *is* the
    information, and a bowed spoke would put a curve between a branch and the
    direction it means.
    """
    start = _edge_point(parent, child.centre_x, child.centre_y)
    end = _edge_point(child, parent.centre_x, parent.centre_y)
    return f"M{_n(start[0])},{_n(start[1])} L{_n(end[0])},{_n(end[1])}"


def _edge_point(box: Box, toward_x: float, toward_y: float) -> tuple[float, float]:
    """Where the ray from ``box``'s centre towards a point leaves the box."""
    dx = toward_x - box.centre_x
    dy = toward_y - box.centre_y
    if dx == 0.0 and dy == 0.0:
        return (box.centre_x, box.centre_y)

    scale_x = (box.width / 2) / abs(dx) if dx else inf
    scale_y = (box.height / 2) / abs(dy) if dy else inf
    reach = min(scale_x, scale_y)
    return (box.centre_x + dx * reach, box.centre_y + dy * reach)


def free_path(style: str, source: Box, target: Box) -> str:
    """The ``d`` of an association - the edge that is not part of the tree.

    Centre to centre whatever the layout is: a free edge says "this also has
    to do with that", and it has no orientation of its own to respect.
    """
    x1, y1 = source.centre_x, source.centre_y
    x2, y2 = target.centre_x, target.centre_y
    if style == "line":
        return f"M{_n(x1)},{_n(y1)} L{_n(x2)},{_n(y2)}"

    mid_x = (x1 + x2) / 2
    mid_y = (y1 + y2) / 2 - abs(x2 - x1) * 0.12
    return f"M{_n(x1)},{_n(y1)} Q{_n(mid_x)},{_n(mid_y)} {_n(x2)},{_n(y2)}"


# ── Bounding box ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Bounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return max(self.max_x - self.min_x, 1.0)

    @property
    def height(self) -> float:
        return max(self.max_y - self.min_y, 1.0)


def bounding_box(
    boxes: Iterable[tuple[float, float, float, float]], padding: float = 48.0
) -> Bounds:
    """The rectangle containing every ``(x, y, width, height)``, plus padding.

    Shared by "fit to screen" and by the SVG export, so a printed map is framed
    exactly the way it is framed on screen.
    """
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for x, y, width, height in boxes:
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x + width)
        max_y = max(max_y, y + height)

    if min_x == float("inf"):
        return Bounds(0.0, 0.0, 1.0, 1.0)
    return Bounds(min_x - padding, min_y - padding, max_x + padding, max_y + padding)
