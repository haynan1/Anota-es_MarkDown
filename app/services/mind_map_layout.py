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
from math import cos, pi, sin

# Breathing room between a parent column and the next, and between siblings.
# Chosen so an arrow has room to curve without the branches touching.
GAP_MAIN = 96.0
GAP_CROSS = 28.0
# Radial rings are wider: the same gap that reads as generous in a column is
# cramped once the nodes fan out around a circle.
RADIAL_RING = 260.0
RADIAL_MIN_ARC = 0.18


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
    mind map), ``down`` (an org chart) or ``radial`` (branches fan out around
    the root).
    """
    if not nodes:
        return {}
    if direction == "radial":
        return _radial(nodes, origin)
    return _orthogonal(nodes, direction, origin)


# ── Orthogonal (right / down) ───────────────────────────────────────────────


def _orthogonal(
    nodes: Sequence[LayoutNode], direction: str, origin: tuple[float, float]
) -> dict[str, tuple[float, float]]:
    """One depth per column (or row); siblings stacked along the cross axis."""
    horizontal = direction != "down"
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
        running += extent[level] + GAP_MAIN

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
            cursor = start + cross_size + GAP_CROSS
            centre = start + cross_size / 2
        else:
            centres = [place(child) for child in kids]
            first, last = centres[0], centres[-1]
            centre = (first[0] + last[0]) / 2
            # A parent taller than the span of its children would spill out of
            # its own column, so the cursor never moves backwards.
            start = centre - cross_size / 2
            cursor = max(cursor, start + cross_size + GAP_CROSS)

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
        cursor += GAP_CROSS * 2

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
