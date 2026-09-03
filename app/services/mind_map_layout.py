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
    # The arrangement this node's own branch uses, or ``None`` to use whatever
    # it hangs from. This is what lets one board hold every kind of map at
    # once - see :func:`effective_layouts`.
    layout: str | None = None
    # Quando presente, esta linha não é um tópico: é um segundo pai para o
    # tópico que ela nomeia.
    #
    # Um tópico que vale para várias etapas primeiro foi desenhado repetido -
    # uma caixa igual embaixo de cada etapa - e sete caixas com o mesmo nome
    # dizem menos do que uma caixa com sete linhas chegando nela. Então a
    # segunda aparição não ocupa lugar no arranjo: o que se desenha é a linha
    # do pai até o tópico de verdade.
    mirror_of: str | None = None


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

    ``direction`` is the map's own arrangement: ``right`` (branches grow to the
    right, the classic mind map), ``down`` (the same map stood on end),
    ``tree`` (a top-down org chart, drawn with square shoulders) or ``radial``
    (branches fan out around the root).

    A node may name its own, and then that branch is arranged that way while
    everything above it stays as it was - see :func:`_compose` and
    :func:`effective_layouts`.

    Um tópico que vale para várias etapas sai da árvore
    ---------------------------------------------------
    Ele não tem *um* lugar na árvore - tem vários - então não disputa lugar
    com ninguém: sai do fluxo, e ele e o ramo dele vão para uma faixa no pé do
    mapa, lado a lado com os outros compartilhados, na ordem em que os pais
    deles aparecem. As linhas descem até lá.

    Tentar encaixá-lo no fluxo *e* puxá-lo para o meio dos pais foi a primeira
    versão, e ela deixava um buraco onde ele estava e o punha por cima de quem
    estivesse embaixo. Um lugar reservado é a única forma de não haver choque.
    """
    if not nodes:
        return {}

    by_all = {node.key: node for node in nodes}
    boxes = [node for node in nodes if not node.mirror_of]
    if not boxes:
        return {}

    parents_of = _shared_parents(nodes, by_all)
    banished = _shared_subtrees(boxes, set(parents_of))

    flow = [node for node in boxes if node.key not in banished]
    if not flow:
        # O mapa inteiro é um tópico compartilhado. Nada a tirar do fluxo.
        flow, banished = boxes, set()

    positions = _compose(build_tree(flow), {n.key: n for n in flow}, direction, origin)
    if banished:
        _place_shared_band(
            positions, boxes, by_all, parents_of, banished, direction, origin
        )
    return positions


def _shared_parents(
    nodes: Sequence[LayoutNode], by_all: dict[str, LayoutNode]
) -> dict[str, list[str]]:
    """Para cada tópico compartilhado, todos os lugares em que ele aparece."""
    found: dict[str, list[str]] = {}
    for node in nodes:
        target = node.mirror_of
        if target and target in by_all and not by_all[target].mirror_of:
            found.setdefault(target, [])
            if node.parent:
                found[target].append(node.parent)
    for key in list(found):
        own = by_all[key].parent
        if own:
            found[key].insert(0, own)
    return found


def _shared_subtrees(boxes: Sequence[LayoutNode], roots: set[str]) -> set[str]:
    """Os tópicos compartilhados e tudo que pende deles."""
    children: dict[str, list[str]] = {}
    for node in boxes:
        if node.parent:
            children.setdefault(node.parent, []).append(node.key)

    banished: set[str] = set()
    stack = list(roots)
    while stack:
        key = stack.pop()
        if key in banished:
            continue
        banished.add(key)
        stack.extend(children.get(key, ()))
    return banished


def _place_shared_band(
    positions: dict[str, tuple[float, float]],
    boxes: Sequence[LayoutNode],
    by_all: dict[str, LayoutNode],
    parents_of: dict[str, list[str]],
    banished: set[str],
    direction: str,
    origin: tuple[float, float],
) -> None:
    """Põe cada tópico compartilhado, com o ramo dele, numa faixa própria.

    A faixa segue o eixo do mapa. Num mapa que desce a página ela é uma
    fileira embaixo; num mapa que cresce para o lado ela é uma coluna à
    direita. Pôr uma faixa horizontal debaixo de um mapa horizontal foi a
    primeira versão, e ela largava o bloco *abaixo e à esquerda* dos próprios
    pais, com o ramo dele apontando de volta para cima - dois eixos brigando
    no mesmo desenho.

    Três decisões, e nenhuma é enfeite:

    * **O eixo é o do mapa.** A profundidade cresce numa direção só, e um
      tópico compartilhado é mais fundo do que todos os pais dele.
    * **O ritmo é o do arranjo.** A árvore usa um vão menor entre níveis do
      que os outros; a faixa usar o vão padrão quebrava a cadência do mapa
      justamente na última linha, que é onde ela mais aparece.
    * **A faixa desce só o quanto precisa.** Ela se afasta do mais fundo que
      *cruza com ela*, e não do mais fundo do mapa: um ramo comprido do outro
      lado do quadro não tem por que empurrar as linhas compartilhadas para
      longe dos tópicos que as chamam.
    """
    by_key = {node.key: node for node in boxes}
    children: dict[str, list[str]] = {}
    for node in boxes:
        if node.parent:
            children.setdefault(node.parent, []).append(node.key)

    horizontal = direction not in VERTICAL_LAYOUTS and direction != "radial"
    gap_main = TREE_GAP_MAIN if direction == "tree" else GAP_MAIN
    gap_cross = TREE_GAP_CROSS if direction == "tree" else GAP_CROSS

    def centre_of(key: str, axis: int) -> float:
        node = by_key[key]
        size = node.width if axis == 0 else node.height
        return positions[key][axis] + size / 2

    cross_axis = 1 if horizontal else 0

    def wanted(key: str) -> float:
        marks = [
            centre_of(parent, cross_axis)
            for parent in parents_of.get(key, [])
            if parent in positions and parent in by_key
        ]
        return sum(marks) / len(marks) if marks else 0.0

    # ── Primeiro o eixo transversal: onde cada bloco fica lado a lado ───────
    #
    # Empacotar aqui não depende de saber onde a faixa começa, e é o que torna
    # possível medir o quanto ela precisa descer: depois deste passo já se sabe
    # que fatia do quadro ela vai ocupar.
    plans: list[tuple[str, dict[str, tuple[float, float]], float, float, float]] = []
    cursor: float | None = None
    for key in sorted(parents_of, key=wanted):
        if key not in by_key:
            continue

        branch = [
            LayoutNode(
                item,
                None if item == key else by_key[item].parent,
                by_key[item].width,
                by_key[item].height,
                by_key[item].collapsed,
                by_key[item].layout,
            )
            for item in _descendants(children, key)
            if item in by_key
        ]
        local = compute_layout(branch, direction, (0.0, 0.0))
        if not local:
            continue

        left = min(x for x, _ in local.values())
        top = min(y for _, y in local.values())
        right = max(x + by_key[item].width for item, (x, _) in local.items())
        bottom = max(y + by_key[item].height for item, (_, y) in local.items())

        span = (bottom - top) if horizontal else (right - left)
        start = wanted(key) - span / 2
        if cursor is not None:
            start = max(start, cursor)
        cursor = start + span + gap_cross * 2
        plans.append((key, local, left if not horizontal else top, start, span))

    if not plans:
        return

    # ── Depois o eixo principal: o quanto a faixa desce (ou anda) ───────────
    band_from = min(start for *_, start, _ in plans)
    band_to = max(start + span for *_, start, span in plans)

    floor = origin[cross_axis ^ 1]
    for key, (x, y) in positions.items():
        node = by_key.get(key)
        if node is None:
            continue
        low, high = (y, y + node.height) if horizontal else (x, x + node.width)
        if high <= band_from or low >= band_to:
            continue  # não cruza com a faixa: não a empurra
        floor = max(floor, (x + node.width) if horizontal else (y + node.height))
    main = floor + gap_main

    for key, local, edge, start, _span in plans:
        for item, (x, y) in local.items():
            if horizontal:
                positions[item] = (x - min(v for v, _ in local.values()) + main,
                                   y - edge + start)
            else:
                positions[item] = (x - edge + start,
                                   y - min(v for _, v in local.values()) + main)


def _descendants(children: dict[str, list[str]], root: str) -> list[str]:
    """``root`` e tudo que pende dele, ele primeiro."""
    order: list[str] = []
    stack = [root]
    seen: set[str] = set()
    while stack:
        key = stack.pop(0)
        if key in seen:
            continue
        seen.add(key)
        order.append(key)
        stack.extend(children.get(key, ()))
    return order


def effective_layouts(
    tree: _Tree, by_key: dict[str, LayoutNode], direction: str
) -> dict[str, str]:
    """What each node is actually arranged by, once inheritance is applied.

    A node that names nothing is arranged by whatever its parent is arranged
    by, and a root that names nothing is arranged by the map. That is what
    makes "arrumar como árvore" still mean the whole map: only the branches
    that were given an opinion of their own keep it.

    Walked breadth-first and iteratively over the whole tree, folded branches
    included. A collapsed branch is not laid out, but it is still exported,
    still read by the outline, and still there the moment it is opened.
    """
    resolved: dict[str, str] = {}
    queue: list[tuple[str, str]] = [(root, direction) for root in tree.roots]
    while queue:
        key, inherited = queue.pop()
        if key in resolved:
            continue
        node = by_key[key]
        own = node.layout if node.layout in BRANCH_ROUTING else None
        mine = own or inherited
        resolved[key] = mine
        queue.extend((child, mine) for child in tree.children.get(key, ()))

    for key in by_key:
        resolved.setdefault(key, direction)
    return resolved


def _parents(tree: _Tree) -> dict[str, str]:
    """Child to parent, taken from the tree rather than from the nodes.

    ``build_tree`` promotes a node with a missing or circular parent to a root,
    and the composition has to agree with it - a node the layout treats as a
    root must not still be looking upwards for an arrangement to inherit.
    """
    parent_of: dict[str, str] = {}
    for parent, children in tree.children.items():
        for child in children:
            parent_of[child] = parent
    return parent_of


def _compose(
    tree: _Tree,
    by_key: dict[str, LayoutNode],
    direction: str,
    origin: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    """Lay out each differently-arranged branch on its own, then fit them.

    A map with one arrangement is one region and this costs nothing. A map
    that mixes them is a region per branch that named its own, and the
    composition is the obvious one: each region is laid out in isolation,
    deepest first, and then handed to the region above it as a single rigid
    block whose size is its bounding box. The parent's own algorithm then
    places that block exactly as it would place a node - so a radial fan
    hanging off a tree gets the slot a large topic would have got, and the
    tidy-tree promise that nothing overlaps survives the mixing.

    The block is placed by its bounding box, and the branch inside it is then
    shifted so its own arrangement is preserved intact. The node the block
    belongs to therefore does *not* land on the block's corner - it lands
    wherever its own arrangement put it, which for a radial branch is the
    middle. That is the point: the connection from above arrives at the node,
    and the fan opens around it.
    """
    parent_of = _parents(tree)
    effective = effective_layouts(tree, by_key, direction)

    # A node opens a region when it names an arrangement that differs from the
    # one it would have inherited. Naming the one it already had changes
    # nothing on screen, so it must not fragment the layout either - a branch
    # split off for no reason is a branch that stops sharing its siblings'
    # column.
    def opens_region(key: str) -> bool:
        own = by_key[key].layout
        if own is None or own not in BRANCH_ROUTING:
            return False
        parent = parent_of.get(key)
        inherited = effective[parent] if parent is not None else direction
        return own != inherited

    # ``None`` is the region that holds the map itself: every root that named
    # nothing, and everything hanging off them that named nothing either.
    walked = _breadth_first(tree)
    region_of: dict[str, str | None] = {}
    for key in walked:
        parent = parent_of.get(key)
        if opens_region(key):
            region_of[key] = key
        elif parent is None:
            region_of[key] = None
        else:
            region_of[key] = region_of[parent]

    members: dict[str | None, list[str]] = {None: []}
    for key in walked:
        members.setdefault(region_of[key], []).append(key)

    depth = _tree_depth(tree)
    local: dict[str | None, dict[str, tuple[float, float]]] = {}
    span: dict[str, tuple[float, float]] = {}
    anchor: dict[str, tuple[float, float]] = {}

    # Deepest first, so a region is always fitted after the blocks it contains
    # have a size to be fitted by.
    for region in sorted(
        members, key=lambda key: -1 if key is None else depth[key], reverse=True
    ):
        # Walked in the tree's own order rather than assembled in two passes,
        # because the order is load-bearing: every arrangement here places
        # siblings in the order the writer put them, and a branch that carries
        # its own arrangement is still one of those siblings. Collecting the
        # members first and appending the blocks afterwards sent every such
        # branch to the end of its row.
        sub: list[LayoutNode] = []
        sizes: dict[str, tuple[float, float]] = {}
        entries = tree.roots if region is None else [region]
        stack: list[tuple[str, str | None]] = [(key, None) for key in reversed(entries)]
        while stack:
            key, inside = stack.pop()
            if region_of[key] != region:
                # A block: whatever is inside it is its own business, and this
                # region treats it as one opaque topic the size of its box.
                width, height = span[key]
                sub.append(LayoutNode(key, inside, width, height, collapsed=True))
                sizes[key] = (width, height)
                continue
            node = by_key[key]
            sub.append(LayoutNode(key, inside, node.width, node.height, node.collapsed))
            sizes[key] = (node.width, node.height)
            if node.collapsed:
                continue
            stack.extend(
                (child, key) for child in reversed(tree.children.get(key, ()))
            )

        arrangement = direction if region is None else effective[region]
        placed = (
            _radial(sub, (0.0, 0.0))
            if arrangement == "radial"
            else _orthogonal(sub, arrangement, (0.0, 0.0))
        )
        local[region] = placed

        if region is not None:
            box = _raw_bounds(placed, sizes)
            anchor[region] = (box[0], box[1])
            span[region] = (box[2] - box[0], box[3] - box[1])

    positions: dict[str, tuple[float, float]] = {}

    def settle(region: str | None, dx: float, dy: float) -> None:
        inside = set(members[region])
        for key, (x, y) in local[region].items():
            if key in inside:
                positions[key] = (x + dx, y + dy)
            else:
                # A block: shift its whole region so that its bounding box
                # lands where this region put it.
                block_x, block_y = anchor[key]
                settle(key, x + dx - block_x, y + dy - block_y)

    settle(None, origin[0], origin[1])
    return positions


def _breadth_first(tree: _Tree) -> list[str]:
    """Every node, parents before children.

    Iterative, because a deep map is not a reason to hit the recursion limit;
    and read with a moving index rather than ``pop(0)``, because popping the
    front of a list shifts the whole list and turns one walk of a map at its
    ceiling into a million pointless moves.
    """
    order: list[str] = list(tree.roots)
    seen: set[str] = set(tree.roots)
    cursor = 0
    while cursor < len(order):
        key = order[cursor]
        cursor += 1
        for child in tree.children.get(key, ()):
            if child in seen:
                continue
            seen.add(child)
            order.append(child)
    return order


def _tree_depth(tree: _Tree) -> dict[str, int]:
    """How deep each node sits, folded branches included."""
    depth: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(root, 0) for root in tree.roots]
    while queue:
        key, level = queue.pop()
        if key in depth:
            continue
        depth[key] = level
        queue.extend((child, level + 1) for child in tree.children.get(key, ()))
    return depth


def _raw_bounds(
    placed: dict[str, tuple[float, float]], sizes: dict[str, tuple[float, float]]
) -> tuple[float, float, float, float]:
    """The rectangle a region occupies, with no padding.

    Unpadded on purpose, unlike :func:`bounding_box`: this one is fitted
    against other branches by an algorithm that adds its own gaps, and padding
    here would be added twice.
    """
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for key, (x, y) in placed.items():
        width, height = sizes.get(key, (0.0, 0.0))
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x + width)
        max_y = max(max_y, y + height)
    if min_x == float("inf"):
        return (0.0, 0.0, 0.0, 0.0)
    return (min_x, min_y, max_x, max_y)


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


# A line, before it is a string.
#
# ``branch_path`` used to build the ``d`` attribute directly, which was fine
# while SVG was the only picture this application produced. It is not any more:
# the same line has to reach a PDF page and a bitmap, and neither of those
# speaks SVG. Parsing back a string this module had just written would have
# been the shortcut, and it would have put a parser between the geometry and
# every drawing made from it.
#
# So the geometry is the geometry, and the ``d`` is one rendering of it. The
# string still comes out byte for byte as it did - the rounding happens at
# serialisation, exactly where it happened before - which is what keeps the
# character-for-character parity test against ``routing.js`` meaningful.


@dataclass(frozen=True, slots=True)
class MoveTo:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class LineTo:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class CurveTo:
    """A cubic Bezier: two control points, then the end."""

    x1: float
    y1: float
    x2: float
    y2: float
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class QuadTo:
    """A quadratic Bezier: one control point, then the end."""

    x1: float
    y1: float
    x: float
    y: float


Segment = MoveTo | LineTo | CurveTo | QuadTo


def branch_segments(routing: str, parent: Box, child: Box) -> tuple[Segment, ...]:
    """The line from ``parent`` to ``child``, as geometry rather than syntax."""
    if routing == "vertical":
        return _vertical_curve(parent, child)
    if routing == "elbow":
        return _elbow(parent, child)
    if routing == "spoke":
        return _spoke(parent, child)
    return _horizontal_curve(parent, child)


def branch_path(routing: str, parent: Box, child: Box) -> str:
    """The ``d`` of the line from ``parent`` to ``child``."""
    return segments_to_path(branch_segments(routing, parent, child))


def segments_to_path(segments: Iterable[Segment]) -> str:
    """Segments as an SVG ``d``. The one place a number becomes a character."""
    return " ".join(_segment_to_path(segment) for segment in segments)


def _segment_to_path(segment: Segment) -> str:
    if isinstance(segment, MoveTo):
        return f"M{_n(segment.x)},{_n(segment.y)}"
    if isinstance(segment, LineTo):
        return f"L{_n(segment.x)},{_n(segment.y)}"
    if isinstance(segment, QuadTo):
        return f"Q{_n(segment.x1)},{_n(segment.y1)} {_n(segment.x)},{_n(segment.y)}"
    return (
        f"C{_n(segment.x1)},{_n(segment.y1)} "
        f"{_n(segment.x2)},{_n(segment.y2)} {_n(segment.x)},{_n(segment.y)}"
    )


def _horizontal_curve(parent: Box, child: Box) -> tuple[Segment, ...]:
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
        MoveTo(x1, y1),
        CurveTo(x1 + reach * lean, y1, x2 - reach * lean, y2, x2, y2),
    )


def _vertical_curve(parent: Box, child: Box) -> tuple[Segment, ...]:
    """The same curve stood on end: out of the bottom face, into the top."""
    y1, y2 = parent.bottom, child.y
    if child.bottom < parent.y:
        y1, y2 = parent.y, child.bottom
    x1, x2 = parent.centre_x, child.centre_x

    reach = max(abs(y2 - y1) * 0.5, CURVE_REACH)
    lean = 1.0 if y2 >= y1 else -1.0
    return (
        MoveTo(x1, y1),
        CurveTo(x1, y1 + reach * lean, x2, y2 - reach * lean, x2, y2),
    )


def _elbow(parent: Box, child: Box) -> tuple[Segment, ...]:
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
            MoveTo(x1, y1),
            LineTo(x1, middle),
            LineTo(x2, middle),
            LineTo(x2, y2),
        )

    first = _sign(middle - y1)
    second = _sign(y2 - middle)
    return (
        MoveTo(x1, y1),
        LineTo(x1, middle - radius * first),
        QuadTo(x1, middle, x1 + radius * across, middle),
        LineTo(x2 - radius * across, middle),
        QuadTo(x2, middle, x2, middle + radius * second),
        LineTo(x2, y2),
    )


def _spoke(parent: Box, child: Box) -> tuple[Segment, ...]:
    """A straight run between two boxes, cut at the faces it crosses.

    Radial is the one layout where a child can sit in any direction at all
    from its parent, so the only honest answer is the line between the two
    centres. It is drawn straight on purpose: in a fan the angle *is* the
    information, and a bowed spoke would put a curve between a branch and the
    direction it means.
    """
    start = _edge_point(parent, child.centre_x, child.centre_y)
    end = _edge_point(child, parent.centre_x, parent.centre_y)
    return (MoveTo(start[0], start[1]), LineTo(end[0], end[1]))


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
