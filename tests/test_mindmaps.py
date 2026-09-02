"""Mapas mentais: pensar em duas dimensões sem perder nada no caminho.

The promises pinned here are the ones a canvas can quietly break:

* a batch of operations is all or nothing - a map never comes back half-edited;
* the hierarchy stays a tree - no gesture can put a branch inside itself;
* a stale client is refused rather than allowed to overwrite;
* everything the writer types is sanitised once, at the service boundary, so
  the canvas, the Markdown export and the SVG export all receive safe values;
* a picture on a board counts as a picture in use - the orphan sweeper must
  not delete it out from under the map.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import threading
import uuid as uuid_module
from math import hypot

import pytest

from app.models import MindMap, MindMapEdge, MindMapNode
from app.repositories.mind_map_repository import MindMapRepository
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.mind_map_export import parse_outline, to_markdown, to_svg
from app.services.mind_map_layout import LayoutNode, bounding_box, compute_layout
from app.services.mind_map_service import MAX_DEPTH, MindMapService


PLACEMENT_SUITE = pathlib.Path(__file__).resolve().parent / "js" / "mindmap-placement.test.mjs"


def new_id() -> str:
    return str(uuid_module.uuid4())


@pytest.fixture()
def mind_map(app):
    return MindMapService.create("Lançamento do produto", "O que precisa acontecer")


@pytest.fixture()
def root(mind_map):
    return MindMapRepository.nodes_of(mind_map)[0]


def add(mind_map, parent=None, revision=None, **fields):
    """Create one node through the operation protocol and return its UUID."""
    identifier = new_id()
    MindMapService.apply_operations(
        mind_map,
        [{"type": "node.create", "uuid": identifier, "parent": parent, "fields": fields}],
        expected_revision=revision,
    )
    return identifier


def node_by_uuid_text(mind_map, text: str) -> MindMapNode | None:
    """The node of this map carrying ``text``, if the map has one."""
    return next(
        (node for node in MindMapRepository.nodes_of(mind_map) if node.text == text), None
    )


def node_by_uuid(public_uuid: str) -> MindMapNode | None:
    from app.extensions import db

    return db.session.scalars(
        db.select(MindMapNode).where(MindMapNode.uuid == public_uuid)
    ).one_or_none()


# ── Ciclo de vida ───────────────────────────────────────────────────────────


class TestLifecycle:
    def test_a_map_is_born_with_its_central_idea(self, app):
        created = MindMapService.create("Arquitetura", "Camadas e limites")

        nodes = MindMapRepository.nodes_of(created)
        assert created.slug == "arquitetura"
        assert created.uuid
        assert created.revision == 1
        assert len(nodes) == 1
        assert nodes[0].text == "Arquitetura"
        assert nodes[0].parent_id is None

    def test_a_nameless_map_is_refused(self, app):
        with pytest.raises(ValidationError):
            MindMapService.create("   ")

    def test_markup_in_a_title_is_stripped(self, app):
        created = MindMapService.create("<script>alert(1)</script>Plano")

        assert "<script>" not in created.title
        assert "Plano" in created.title

    def test_two_maps_with_the_same_name_get_different_addresses(self, app):
        first = MindMapService.create("Plano")
        second = MindMapService.create("Plano")

        assert first.slug != second.slug

    def test_an_invalid_colour_falls_back_to_the_default(self, app):
        created = MindMapService.create("Cores", color="javascript:alert(1)")
        assert created.color == "#4F46E5"

    def test_the_trash_is_reversible(self, app, mind_map):
        MindMapService.soft_delete(mind_map)
        assert MindMapRepository.get_by_uuid(mind_map.uuid) is None
        assert MindMapRepository.listing(deleted=True)

        MindMapService.restore(mind_map)
        assert MindMapRepository.get_by_uuid(mind_map.uuid) is not None

    def test_purging_takes_the_nodes_with_it(self, app, db, mind_map, root):
        add(mind_map, parent=root.uuid, text="Marketing")
        map_id = mind_map.id

        MindMapService.purge(mind_map)

        assert db.session.get(MindMap, map_id) is None
        assert (
            db.session.scalars(
                db.select(MindMapNode).where(MindMapNode.map_id == map_id)
            ).all()
            == []
        )

    def test_a_copy_carries_the_whole_graph(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Marketing", color="#22C55E")
        other = add(mind_map, parent=root.uuid, text="Engenharia")
        MindMapService.apply_operations(
            mind_map,
            [{"type": "edge.create", "uuid": new_id(), "source": child,
              "target": other, "fields": {"label": "depende"}}],
        )

        clone = MindMapService.duplicate(mind_map)
        graph = MindMapService.graph_payload(clone)

        assert clone.uuid != mind_map.uuid
        assert len(graph["nodes"]) == 3
        assert len(graph["edges"]) == 1
        # The copy is independent: its nodes are new rows with new identities.
        original_uuids = {node["uuid"] for node in MindMapService.graph_payload(mind_map)["nodes"]}
        assert not original_uuids & {node["uuid"] for node in graph["nodes"]}


# ── O protocolo de operações ────────────────────────────────────────────────


class TestOperations:
    def test_a_batch_bumps_the_revision_once(self, app, mind_map, root):
        MindMapService.apply_operations(
            mind_map,
            [
                {"type": "node.create", "uuid": new_id(), "parent": root.uuid},
                {"type": "node.create", "uuid": new_id(), "parent": root.uuid},
            ],
            expected_revision=1,
        )
        assert mind_map.revision == 2

    def test_an_empty_batch_changes_nothing(self, app, mind_map):
        result = MindMapService.apply_operations(mind_map, [], expected_revision=1)

        assert result.applied == 0
        assert mind_map.revision == 1

    def test_a_stale_client_is_refused_with_the_server_graph(self, app, mind_map, root):
        add(mind_map, parent=root.uuid, text="Já salvo")

        with pytest.raises(ConflictError) as excinfo:
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": root.uuid, "fields": {"text": "x"}}],
                expected_revision=1,
            )

        # The rejection carries what the client needs to recover without a
        # second request.
        assert excinfo.value.server_state["revision"] == mind_map.revision
        assert len(excinfo.value.server_state["nodes"]) == 2

    def test_creating_the_same_node_twice_updates_instead_of_duplicating(
        self, app, mind_map, root
    ):
        identifier = new_id()
        operation = {
            "type": "node.create",
            "uuid": identifier,
            "parent": root.uuid,
            "fields": {"text": "Primeiro"},
        }
        MindMapService.apply_operations(mind_map, [operation])
        MindMapService.apply_operations(
            mind_map, [{**operation, "fields": {"text": "Segundo"}}]
        )

        nodes = MindMapRepository.nodes_of(mind_map)
        assert len(nodes) == 2
        assert node_by_uuid(identifier).text == "Segundo"

    def test_a_failed_operation_rolls_the_whole_batch_back(self, app, mind_map, root):
        good = new_id()

        with pytest.raises(NotFoundError):
            MindMapService.apply_operations(
                mind_map,
                [
                    {"type": "node.create", "uuid": good, "parent": root.uuid,
                     "fields": {"text": "Existe"}},
                    {"type": "node.update", "uuid": new_id(), "fields": {"text": "x"}},
                ],
            )

        assert node_by_uuid(good) is None
        assert mind_map.revision == 1

    def test_an_unknown_operation_is_refused(self, app, mind_map):
        with pytest.raises(ValidationError):
            MindMapService.apply_operations(mind_map, [{"type": "node.explode"}])

    def test_a_malformed_identifier_never_reaches_a_query(self, app, mind_map):
        for bad in ["", "../../etc/passwd", "' OR 1=1 --", 42, None]:
            with pytest.raises(ValidationError):
                MindMapService.apply_operations(
                    mind_map, [{"type": "node.update", "uuid": bad, "fields": {}}]
                )

    def test_too_many_operations_are_refused(self, app, mind_map, root):
        batch = [{"type": "node.create", "uuid": new_id(), "parent": root.uuid}] * 501

        with pytest.raises(ValidationError) as excinfo:
            MindMapService.apply_operations(mind_map, batch)

        assert "500" in excinfo.value.message


# ── A hierarquia ────────────────────────────────────────────────────────────


class TestHierarchy:
    def test_a_node_cannot_become_its_own_descendant(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Filho")
        grandchild = add(mind_map, parent=child, text="Neto")

        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map, [{"type": "node.move", "uuid": child, "parent": grandchild}]
            )

    def test_a_node_cannot_be_its_own_parent(self, app, mind_map, root):
        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map, [{"type": "node.move", "uuid": root.uuid, "parent": root.uuid}]
            )

    def test_depth_is_bounded(self, app, mind_map, root):
        parent = root.uuid
        for _ in range(MAX_DEPTH - 1):
            parent = add(mind_map, parent=parent, text="Nível")

        with pytest.raises(ValidationError) as excinfo:
            add(mind_map, parent=parent, text="Fundo demais")

        assert str(MAX_DEPTH) in excinfo.value.message

    def test_deleting_a_branch_takes_the_branch(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Ramo")
        grandchild = add(mind_map, parent=child, text="Folha")

        MindMapService.apply_operations(
            mind_map, [{"type": "node.delete", "uuid": child}]
        )

        assert node_by_uuid(child) is None
        assert node_by_uuid(grandchild) is None
        assert node_by_uuid(root.uuid) is not None

    def test_promoting_keeps_the_children(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Intermediário")
        grandchild = add(mind_map, parent=child, text="Sobrevivente")

        MindMapService.apply_operations(
            mind_map, [{"type": "node.delete", "uuid": child, "mode": "promote"}]
        )

        survivor = node_by_uuid(grandchild)
        assert survivor is not None
        assert survivor.parent_id == root.id

    def test_deleting_something_already_gone_is_not_an_error(self, app, mind_map):
        result = MindMapService.apply_operations(
            mind_map, [{"type": "node.delete", "uuid": new_id()}]
        )
        assert result.applied == 1

    def test_dragging_a_topic_to_the_top_level_keeps_it(self, app, mind_map, root):
        """The gesture that used to delete a branch.

        ``children`` carried ``delete-orphan``, so detaching a child from its
        parent did not free it - it destroyed it. Dragging a topic out to the
        top level is exactly that detachment, and the canvas sends it as
        ``node.move`` with a null parent: the topic and everything under it
        vanished, silently, on an ordinary drag.
        """
        parent = add(mind_map, parent=root.uuid, text="Ramo")
        child = add(mind_map, parent=parent, text="Folha")

        MindMapService.apply_operations(
            mind_map, [{"type": "node.move", "uuid": parent, "parent": None}]
        )

        assert node_by_uuid(parent) is not None
        assert node_by_uuid(parent).parent_id is None
        # The branch travels with the topic it hangs from.
        assert node_by_uuid(child) is not None
        assert node_by_uuid(child).parent_id == node_by_uuid(parent).id
        assert MindMapRepository.node_count(mind_map.id) == 3

    def test_a_branch_is_still_deleted_when_deletion_is_what_was_asked(
        self, app, mind_map, root
    ):
        """The other half: dropping delete-orphan must not weaken a real delete.

        The branch goes through the database's ``ON DELETE CASCADE``, which is
        what ``passive_deletes`` defers to - not through the ORM cascade.
        """
        parent = add(mind_map, parent=root.uuid, text="Ramo")
        child = add(mind_map, parent=parent, text="Folha")

        MindMapService.apply_operations(
            mind_map, [{"type": "node.delete", "uuid": parent}]
        )

        assert node_by_uuid(parent) is None
        assert node_by_uuid(child) is None
        assert MindMapRepository.node_count(mind_map.id) == 1

    def test_moving_a_node_reorders_its_new_siblings(self, app, mind_map, root):
        first = add(mind_map, parent=root.uuid, text="A")
        second = add(mind_map, parent=root.uuid, text="B")

        MindMapService.apply_operations(
            mind_map, [{"type": "node.move", "uuid": second, "position": 0}]
        )

        positions = {node.uuid: node.position for node in MindMapRepository.nodes_of(mind_map)}
        assert positions[second] < positions[first]


# ── Conexões livres ─────────────────────────────────────────────────────────


class TestConnections:
    def test_two_nodes_can_be_associated(self, app, mind_map, root):
        left = add(mind_map, parent=root.uuid, text="Esquerda")
        right = add(mind_map, parent=root.uuid, text="Direita")

        MindMapService.apply_operations(
            mind_map,
            [{"type": "edge.create", "uuid": new_id(), "source": left,
              "target": right, "fields": {"label": "alimenta", "style": "dashed"}}],
        )

        edges = MindMapService.graph_payload(mind_map)["edges"]
        assert len(edges) == 1
        assert edges[0]["label"] == "alimenta"
        assert edges[0]["style"] == "dashed"

    def test_a_node_cannot_be_connected_to_itself(self, app, mind_map, root):
        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "edge.create", "uuid": new_id(),
                  "source": root.uuid, "target": root.uuid}],
            )

    def test_the_same_association_is_never_drawn_twice(self, app, mind_map, root):
        other = add(mind_map, parent=root.uuid, text="Outro")
        pair = {"source": root.uuid, "target": other}

        MindMapService.apply_operations(
            mind_map, [{"type": "edge.create", "uuid": new_id(), **pair}]
        )
        MindMapService.apply_operations(
            mind_map,
            [{"type": "edge.create", "uuid": new_id(), **pair,
              "fields": {"label": "segunda tentativa"}}],
        )

        edges = MindMapService.graph_payload(mind_map)["edges"]
        assert len(edges) == 1
        assert edges[0]["label"] == "segunda tentativa"

    def test_an_edge_dies_with_the_node_it_touched(self, app, db, mind_map, root):
        other = add(mind_map, parent=root.uuid, text="Outro")
        MindMapService.apply_operations(
            mind_map,
            [{"type": "edge.create", "uuid": new_id(), "source": root.uuid, "target": other}],
        )

        MindMapService.apply_operations(mind_map, [{"type": "node.delete", "uuid": other}])

        assert db.session.scalars(db.select(MindMapEdge)).all() == []

    def test_an_invalid_style_is_refused(self, app, mind_map, root):
        other = add(mind_map, parent=root.uuid)
        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "edge.create", "uuid": new_id(), "source": root.uuid,
                  "target": other, "fields": {"style": "neon"}}],
            )


# ── Valores que entram ──────────────────────────────────────────────────────


class TestSanitising:
    def test_markup_in_a_topic_is_stripped(self, app, mind_map, root):
        identifier = add(mind_map, parent=root.uuid, text="<img src=x onerror=alert(1)>Ideia")

        assert "<img" not in node_by_uuid(identifier).text
        assert "Ideia" in node_by_uuid(identifier).text

    def test_a_note_keeps_its_line_breaks(self, app, mind_map, root):
        identifier = add(mind_map, parent=root.uuid, note="Primeira linha\nSegunda linha")

        assert node_by_uuid(identifier).note == "Primeira linha\nSegunda linha"

    def test_a_script_url_is_refused(self, app, mind_map, root):
        for bad in ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd"]:
            with pytest.raises(ValidationError):
                MindMapService.apply_operations(
                    mind_map,
                    [{"type": "node.update", "uuid": root.uuid, "fields": {"url": bad}}],
                )

    def test_an_https_url_is_kept(self, app, mind_map, root):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"url": "https://exemplo.com/plano"}}],
        )
        assert node_by_uuid(root.uuid).url == "https://exemplo.com/plano"

    def test_a_remote_image_must_be_http(self, app, mind_map, root):
        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": root.uuid,
                  "fields": {"image_url": "mailto:alguem@exemplo.com"}}],
            )

    def test_coordinates_are_clamped(self, app, mind_map, root):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"x": 10 ** 12, "y": -(10 ** 12), "width": 99999}}],
        )

        node = node_by_uuid(root.uuid)
        assert node.x == 100_000
        assert node.y == -100_000
        assert node.width == 640

    def test_a_nonsense_number_does_not_poison_the_geometry(self, app, mind_map, root):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid, "fields": {"x": "muito à direita"}}],
        )
        assert node_by_uuid(root.uuid).x == -100_000 or node_by_uuid(root.uuid).x == 0

    def test_an_invalid_shape_is_refused(self, app, mind_map, root):
        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": root.uuid, "fields": {"shape": "estrela"}}],
            )


# ── Imagens e documentos ────────────────────────────────────────────────────


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture()
def image_asset(app):
    import io

    from werkzeug.datastructures import FileStorage

    from app.services.media_service import store_upload

    return store_upload(FileStorage(io.BytesIO(PNG), filename="foto.png"))


class TestAttachments:
    def test_an_upload_can_be_placed_on_a_topic(self, app, mind_map, root, image_asset):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"media_uuid": image_asset.uuid}}],
        )

        node = node_by_uuid(root.uuid)
        assert node.media_asset_id == image_asset.id
        # Attaching a picture is enough; nobody has to also say "this is now an
        # image node".
        assert node.kind == "image"

        payload = MindMapService.graph_payload(mind_map)["nodes"][0]
        # Identities, not addresses: the page holds the URL shape. See
        # ``_node_payload``.
        assert payload["media_uuid"] == image_asset.uuid

    def test_only_images_can_be_placed_on_a_topic(self, app, mind_map, root):
        import io

        from werkzeug.datastructures import FileStorage

        from app.services.media_service import store_upload

        pdf = store_upload(FileStorage(io.BytesIO(b"%PDF-1.4 fake"), filename="a.pdf"))

        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": root.uuid,
                  "fields": {"media_uuid": pdf.uuid}}],
            )

    def test_a_picture_on_a_canvas_is_not_an_orphan(self, app, db, mind_map, root, image_asset):
        """The sweeper reads document bodies; a canvas holds its pictures by
        foreign key, and must declare them or lose them."""
        from datetime import timedelta

        from app.services.media_service import prune_orphans
        from app.utils.dates import utcnow

        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"media_uuid": image_asset.uuid}}],
        )
        image_asset.created_at = utcnow() - timedelta(days=3)
        db.session.commit()

        assert prune_orphans() == (0, 0)

    def test_purging_a_map_reclaims_the_pictures_only_it_held(
        self, app, db, mind_map, root, image_asset
    ):
        from app.models import MediaAsset
        from app.services.media_service import asset_exists

        path_existed = asset_exists(image_asset)
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"media_uuid": image_asset.uuid}}],
        )
        asset_id = image_asset.id

        MindMapService.purge(mind_map)

        assert path_existed
        assert db.session.get(MediaAsset, asset_id) is None

    def test_a_picture_shared_with_a_document_survives_the_purge(
        self, app, db, mind_map, root, image_asset, make_document
    ):
        from app.models import MediaAsset

        make_document(title="Também usa", content=f"![foto](/midia/{image_asset.uuid})")
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"media_uuid": image_asset.uuid}}],
        )
        asset_id = image_asset.id

        MindMapService.purge(mind_map)

        assert db.session.get(MediaAsset, asset_id) is not None

    def test_a_topic_can_point_at_a_document(self, app, mind_map, root, document):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"document_uuid": document.uuid}}],
        )

        payload = MindMapService.graph_payload(mind_map)["nodes"][0]
        assert payload["document"]["title"] == document.title
        assert payload["document"]["uuid"] == document.uuid

    def test_a_trashed_document_is_not_offered_as_a_link(
        self, app, mind_map, root, document
    ):
        from app.services.document_service import DocumentService

        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "fields": {"document_uuid": document.uuid}}],
        )
        DocumentService.move_to_trash(document)

        payload = MindMapService.graph_payload(mind_map)["nodes"][0]
        assert payload["document"] is None

    def test_an_unknown_document_is_refused(self, app, mind_map, root):
        with pytest.raises(NotFoundError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": root.uuid,
                  "fields": {"document_uuid": new_id()}}],
            )


# ── Arrumação ───────────────────────────────────────────────────────────────


class TestLayout:
    def test_a_tidy_tree_has_no_overlapping_siblings(self):
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            *[LayoutNode(f"f{index}", "raiz", 180, 48) for index in range(6)],
        ]
        placed = compute_layout(nodes, "right")

        boxes = [(placed[f"f{index}"][1], placed[f"f{index}"][1] + 48) for index in range(6)]
        boxes.sort()
        for (_, bottom), (top, _) in zip(boxes, boxes[1:]):
            assert top >= bottom

    def test_children_sit_to_the_right_of_their_parent(self):
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            LayoutNode("filho", "raiz", 180, 48),
            LayoutNode("neto", "filho", 180, 48),
        ]
        placed = compute_layout(nodes, "right")

        assert placed["filho"][0] > placed["raiz"][0]
        assert placed["neto"][0] > placed["filho"][0]

    def test_a_vertical_layout_flips_the_axes(self):
        nodes = [LayoutNode("raiz", None, 180, 48), LayoutNode("filho", "raiz", 180, 48)]
        placed = compute_layout(nodes, "down")

        assert placed["filho"][1] > placed["raiz"][1]

    def test_a_collapsed_branch_reserves_no_space(self):
        expanded = compute_layout(
            [
                LayoutNode("raiz", None, 180, 48),
                LayoutNode("a", "raiz", 180, 48),
                LayoutNode("a1", "a", 180, 48),
                LayoutNode("a2", "a", 180, 48),
                LayoutNode("b", "raiz", 180, 48),
            ],
            "right",
        )
        folded = compute_layout(
            [
                LayoutNode("raiz", None, 180, 48),
                LayoutNode("a", "raiz", 180, 48, collapsed=True),
                LayoutNode("a1", "a", 180, 48),
                LayoutNode("a2", "a", 180, 48),
                LayoutNode("b", "raiz", 180, 48),
            ],
            "right",
        )

        assert folded["b"][1] < expanded["b"][1]

    def test_the_radial_layout_pushes_each_level_further_out(self):
        """Radial is offered in the UI and was the one layout with no test.

        It is also the only one that is recursive and trigonometric, so the
        property worth pinning is the one a reader checks by eye: a child sits
        on a wider ring than its parent, and the centre stays the centre.
        """
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            LayoutNode("a", "raiz", 180, 48),
            LayoutNode("b", "raiz", 180, 48),
            LayoutNode("a1", "a", 180, 48),
        ]
        placed = compute_layout(nodes, "radial")

        assert set(placed) == {"raiz", "a", "b", "a1"}

        def radius(key):
            x, y = placed[key]
            return hypot(x + 90 - placed["raiz"][0] - 90, y + 24 - placed["raiz"][1] - 24)

        assert radius("raiz") == pytest.approx(0, abs=1e-6)
        assert radius("a") > radius("raiz")
        assert radius("a1") > radius("a")

    def test_radial_siblings_do_not_land_on_each_other(self):
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            *[LayoutNode(f"f{index}", "raiz", 180, 48) for index in range(8)],
        ]
        placed = compute_layout(nodes, "radial")

        spots = [placed[f"f{index}"] for index in range(8)]
        assert len(set(spots)) == len(spots), "dois ramos no mesmo ponto"

    def test_a_collapsed_branch_reserves_no_radial_space(self):
        """The same promise the other two layouts already make."""
        expanded = compute_layout(
            [
                LayoutNode("raiz", None, 180, 48),
                LayoutNode("a", "raiz", 180, 48),
                *[LayoutNode(f"a{index}", "a", 180, 48) for index in range(5)],
                LayoutNode("b", "raiz", 180, 48),
            ],
            "radial",
        )
        collapsed = compute_layout(
            [
                LayoutNode("raiz", None, 180, 48),
                LayoutNode("a", "raiz", 180, 48, collapsed=True),
                *[LayoutNode(f"a{index}", "a", 180, 48) for index in range(5)],
                LayoutNode("b", "raiz", 180, 48),
            ],
            "radial",
        )

        # With the branch shut, "b" no longer has to make room for five leaves.
        assert collapsed["b"] != expanded["b"]

    def test_extra_roots_get_their_own_radial_centre(self):
        placed = compute_layout(
            [LayoutNode("r1", None, 180, 48), LayoutNode("r2", None, 180, 48)],
            "radial",
        )

        assert placed["r1"] != placed["r2"]

    def test_a_radial_cycle_does_not_hang(self):
        """A doctored database can name two nodes as each other's parent."""
        placed = compute_layout(
            [LayoutNode("a", "b", 100, 40), LayoutNode("b", "a", 100, 40)], "radial"
        )

        assert set(placed) == {"a", "b"}

    def test_every_layout_places_every_node(self):
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            LayoutNode("a", "raiz", 180, 48),
            LayoutNode("a1", "a", 180, 48),
        ]

        for direction in ("right", "down", "radial"):
            placed = compute_layout(nodes, direction)
            assert set(placed) == {"raiz", "a", "a1"}, direction

    def test_a_broken_graph_still_lays_out(self):
        """A cycle cannot be created through the service - but a layout pass is
        not the place to discover a hand-edited database."""
        placed = compute_layout(
            [LayoutNode("a", "b", 100, 40), LayoutNode("b", "a", 100, 40)], "right"
        )
        assert set(placed) == {"a", "b"}

    def test_an_empty_map_has_no_coordinates(self):
        assert compute_layout([], "right") == {}

    def test_the_bounding_box_covers_every_node(self):
        box = bounding_box([(0, 0, 100, 50), (300, 200, 100, 50)], padding=10)

        assert box.min_x == -10
        assert box.max_x == 410
        assert box.max_y == 260

    def test_organising_a_real_map_moves_its_nodes(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Ramo", x=0, y=0)

        MindMapService.autolayout(mind_map, "right")

        assert node_by_uuid(child).x > node_by_uuid(root.uuid).x
        assert mind_map.revision == 3


# ── Exportação e importação ─────────────────────────────────────────────────


class TestExchange:
    def test_the_outline_reflects_the_hierarchy(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Marketing")
        add(mind_map, parent=child, text="Landing page", url="https://exemplo.com")

        markdown = MindMapService.export_markdown(mind_map)

        assert "# Lançamento do produto" in markdown
        assert "- Marketing" in markdown
        assert "  - [Landing page](https://exemplo.com)" in markdown

    def test_a_label_cannot_smuggle_markdown_syntax(self, app, mind_map, root):
        add(mind_map, parent=root.uuid, text="Um [link](http://mau) e *ênfase*")

        markdown = MindMapService.export_markdown(mind_map)

        assert "\\[link\\]" in markdown
        assert "\\*ênfase\\*" in markdown

    def test_the_drawing_escapes_everything_it_is_given(self, app, db, mind_map, root):
        """Markup never survives the service, so the exporter is fed a value
        straight into the column: an SVG is a document a browser executes, and
        it must escape on its own rather than trusting whoever filled the row."""
        node = node_by_uuid(root.uuid)
        node.text = 'Aspas " e <tag> & E-comercial'
        mind_map.title = "<script>alert(1)</script>"
        db.session.commit()

        drawing = MindMapService.export_svg(mind_map)

        assert "<tag>" not in drawing
        assert "&lt;tag&gt;" in drawing
        assert "&amp;" in drawing
        assert "<script>" not in drawing
        assert drawing.startswith("<?xml")

    def test_a_hostile_colour_never_reaches_the_drawing(self, app, mind_map, root):
        # The service refuses it on the way in; this pins that the exporter
        # does not trust the column either.
        node = node_by_uuid(root.uuid)
        node.color = 'red" onload="alert(1)'
        from app.extensions import db

        db.session.commit()

        drawing = to_svg(mind_map, MindMapRepository.nodes_of(mind_map), [])
        assert "onload" not in drawing

    def test_an_empty_map_still_draws(self, app, mind_map):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.delete",
              "uuid": MindMapRepository.nodes_of(mind_map)[0].uuid}],
        )

        drawing = MindMapService.export_svg(mind_map)
        assert "Mapa sem tópicos" in drawing

    def test_headings_and_bullets_become_an_outline(self):
        items = parse_outline(
            "# Título\n\nTexto solto.\n\n## Seção\n\n- Item\n  - Subitem\n\n## Outra\n"
        )

        assert [item.text for item in items] == [
            "Título", "Seção", "Item", "Subitem", "Outra",
        ]
        assert items[0].depth == 0
        assert items[2].depth == 2
        assert items[3].depth == 3

    def test_a_link_in_an_outline_is_kept(self):
        items = parse_outline("- [Documentação](https://exemplo.com/doc)")

        assert items[0].text == "Documentação"
        assert items[0].url == "https://exemplo.com/doc"

    def test_prose_alone_produces_nothing(self):
        assert parse_outline("Só um parágrafo, sem estrutura nenhuma.") == []

    def test_a_document_becomes_a_map(self, app, make_document):
        document = make_document(
            title="Manual",
            content="# Manual\n\n## Instalação\n\n- Requisitos\n\n## Uso\n",
        )

        created = MindMapService.from_document(document)
        nodes = MindMapRepository.nodes_of(created)

        assert created.title == "Manual"
        # The root the map is born with, plus one node per outline entry.
        assert len(nodes) == 5
        assert {node.text for node in nodes} >= {"Instalação", "Requisitos", "Uso"}

    def test_a_document_without_structure_is_refused(self, app, make_document):
        document = make_document(title="Sem títulos", content="Apenas texto corrido.")

        with pytest.raises(ValidationError):
            MindMapService.from_document(document)

    def test_a_map_becomes_a_document(self, app, mind_map, root):
        add(mind_map, parent=root.uuid, text="Marketing")

        document = MindMapService.to_document(mind_map)

        assert document.title == mind_map.title
        assert "- Marketing" in document.content_markdown


# ── Limites ─────────────────────────────────────────────────────────────────


class TestLimits:
    def test_a_map_has_a_node_ceiling(self, app, monkeypatch, mind_map, root):
        import app.services.mind_map_service as service

        monkeypatch.setattr(service, "MAX_NODES_PER_MAP", 3)

        add(mind_map, parent=root.uuid)
        add(mind_map, parent=root.uuid)

        with pytest.raises(ValidationError) as excinfo:
            add(mind_map, parent=root.uuid)

        assert "comporta" in excinfo.value.message.lower()

    def test_the_camera_is_remembered_without_bumping_the_revision(self, app, mind_map):
        MindMapService.save_viewport(mind_map, x=120, y=-40, zoom=1.5)

        assert mind_map.viewport_x == 120
        assert mind_map.viewport_zoom == 1.5
        # Panning is not an edit; making it one would invalidate every other
        # tab's batch on every scroll.
        assert mind_map.revision == 1

    def test_an_absurd_zoom_is_clamped(self, app, mind_map):
        MindMapService.save_viewport(mind_map, x=0, y=0, zoom=1000)
        assert mind_map.viewport_zoom == 4


# ── HTTP ────────────────────────────────────────────────────────────────────


class TestRoutes:
    def test_the_gallery_lists_maps(self, client, mind_map):
        response = client.get("/mapas/")

        assert response.status_code == 200
        assert "Lançamento do produto" in response.get_data(as_text=True)

    def test_the_canvas_ships_the_graph_with_the_page(self, client, mind_map):
        """A board that renders empty for one round trip reads as a bug."""
        response = client.get(f"/mapas/{mind_map.uuid}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "data-graph=" in body
        assert mind_map.uuid in body

    def test_an_unknown_map_is_a_404(self, client):
        assert client.get(f"/mapas/{new_id()}").status_code == 404

    def test_a_map_can_be_created_from_the_gallery(self, client):
        response = client.post(
            "/mapas/", data={"title": "Novo mapa", "color": "#22C55E", "layout": "radial"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert MindMapRepository.listing(search="Novo mapa")

    def test_the_markdown_export_is_a_download(self, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}/markdown")

        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.mimetype == "text/markdown"

    def test_the_drawing_is_never_served_inline(self, client, mind_map):
        """An SVG is a document a browser executes. It leaves as a download,
        sandboxed, whatever the escaping upstream already guarantees."""
        response = client.get(f"/mapas/{mind_map.uuid}/svg")

        assert "attachment" in response.headers["Content-Disposition"]
        assert response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_a_batch_is_applied_over_http(self, client, mind_map, root):
        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            json={
                "revision": 1,
                "operations": [
                    {"type": "node.create", "uuid": new_id(), "parent": root.uuid,
                     "fields": {"text": "Pela rede"}}
                ],
            },
        )

        assert response.status_code == 200
        assert response.get_json()["revision"] == 2

    def test_a_stale_batch_answers_409_with_the_graph(self, client, mind_map, root):
        add(mind_map, parent=root.uuid)

        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            json={"revision": 1, "operations": [
                {"type": "node.update", "uuid": root.uuid, "fields": {"text": "x"}}
            ]},
        )

        assert response.status_code == 409
        assert response.get_json()["server_state"]["nodes"]

    def test_operations_must_be_a_list(self, client, mind_map):
        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes", json={"operations": "tudo"}
        )
        assert response.status_code == 400

    def test_the_trash_round_trip_works_over_http(self, client, mind_map):
        client.post(f"/mapas/{mind_map.uuid}/excluir")
        assert MindMapRepository.get_by_uuid(mind_map.uuid) is None

        client.post(f"/mapas/{mind_map.uuid}/restaurar")
        assert MindMapRepository.get_by_uuid(mind_map.uuid) is not None

    def test_a_next_parameter_cannot_leave_the_application(self, client, mind_map):
        """Every action here returns the user where they were; a `next` holding
        a full URL would turn each of them into an open redirect."""
        response = client.post(
            f"/mapas/{mind_map.uuid}/favoritar", data={"next": "https://exemplo.mau/"}
        )

        assert response.status_code == 302
        assert not response.headers["Location"].startswith("https://exemplo.mau")

    def test_the_canvas_api_is_csrf_protected(self, csrf_app, mind_map):
        """The editor's fetch layer sends the token as a header; a request
        without one is a cross-site request."""
        with csrf_app.test_client() as client:
            created = MindMapService.create("Protegido")
            response = client.post(
                f"/api/mapas/{created.uuid}/operacoes",
                json={"revision": 1, "operations": []},
            )
            assert response.status_code == 400


# ── Fronteiras ──────────────────────────────────────────────────────────────


class TestTheMarkdownExportEscapesEverything:
    """The exporter's own rule, applied to every value and not only the label.

    A note went out raw, so a line beginning "- " or "#" stopped being a note
    and became structure the moment the file was read back. A URL and a
    document title went out raw too, and either could end the construct it sat
    inside. None of it is XSS - the render pipeline sanitises on the way in -
    but an export that does not round-trip loses the writer's work quietly.
    """

    def test_a_note_that_looks_like_a_list_stays_a_note(self, app, mind_map, root):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "note": "- nao sou um item\n# nem um titulo"}],
        )

        out = MindMapService.export_markdown(mind_map)

        assert "\\- nao sou um item" in out
        assert "\\# nem um titulo" in out

    def test_a_url_with_a_bracket_does_not_end_the_link(self, app, mind_map, root):
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "url": "https://exemplo.test/a(b)c"}],
        )

        out = MindMapService.export_markdown(mind_map)

        assert "(<https://exemplo.test/a(b)c>)" in out

    def test_a_bracketed_link_still_round_trips(self, app, mind_map, root):
        """Wrapping on the way out is only correct if reading back unwraps."""
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid,
              "url": "https://exemplo.test/a(b)c"}],
        )

        items = parse_outline(MindMapService.export_markdown(mind_map))

        assert any(item.url == "https://exemplo.test/a(b)c" for item in items)

    def test_an_ordinary_url_is_not_wrapped(self, app, mind_map, root):
        """Brackets on every link would be noise on the ninety-nine that are fine."""
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid, "url": "https://exemplo.test/a"}],
        )

        out = MindMapService.export_markdown(mind_map)

        assert "](https://exemplo.test/a)" in out

    def test_a_document_title_cannot_end_its_wikilink(
        self, app, mind_map, root, make_document
    ):
        document = make_document(title="Fecha ]] aqui")
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid, "document_uuid": document.uuid}],
        )

        out = MindMapService.export_markdown(mind_map)
        line = next(l for l in out.splitlines() if "Documento:" in l)

        assert line.count("]]") == 1
        assert line.rstrip().endswith("]]")

    def test_a_backslash_is_escaped_before_anything_else(self, app, mind_map, root):
        """Escaping it last would double every escape just added."""
        MindMapService.apply_operations(
            mind_map, [{"type": "node.update", "uuid": root.uuid, "note": "c:\\rota"}]
        )

        out = MindMapService.export_markdown(mind_map)

        assert "c:\\\\rota" in out

    def test_an_image_node_exports_the_url_the_application_serves(
        self, app, mind_map, root, image_asset
    ):
        """The prefix comes from the sanitiser, which owns the one path
        uploaded media is ever served from - hardcoding it here would let the
        export drift into pointing at a URL that answers 404."""
        from app.services.sanitizer import MEDIA_URL_PREFIX

        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": root.uuid, "media_uuid": image_asset.uuid}],
        )

        out = MindMapService.export_markdown(mind_map)

        assert f"]({MEDIA_URL_PREFIX}{image_asset.uuid})" in out

    def test_an_ordinary_outline_is_untouched_by_all_of_this(self, app, mind_map, root):
        """The escaping must not show up in text that never needed it."""
        child = add(mind_map, parent=root.uuid, text="Um ramo comum")
        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": child, "note": "Uma anotacao normal."}],
        )

        out = MindMapService.export_markdown(mind_map)

        assert "- Um ramo comum" in out
        assert "Uma anotacao normal." in out
        assert "\\" not in out


class TestOneMapCannotReachAnother:
    """The map in the URL is the only map an operation may touch.

    Every operation names its target by a client-minted UUID, so the batch has
    to be the thing that scopes them. It reads one map's nodes and resolves
    against that dictionary alone - which means a UUID belonging to another
    board simply does not exist. Structural, and therefore worth pinning:
    a refactor that resolved UUIDs with a global query would still pass every
    other test in this file.
    """

    @pytest.fixture()
    def other(self, app):
        return MindMapService.create("Outro mapa", "")

    def test_a_foreign_topic_cannot_be_edited(self, app, mind_map, other):
        stranger = MindMapRepository.nodes_of(other)[0]

        with pytest.raises(NotFoundError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": stranger.uuid, "text": "invadido"}],
            )

        assert node_by_uuid(stranger.uuid).text == other.title

    def test_a_foreign_topic_cannot_be_deleted(self, app, mind_map, other):
        """A delete naming an unknown topic is a no-op, not a reach.

        ``node.delete`` is deliberately idempotent - a retried batch must not
        fail on the second pass - so this does not raise. What matters is that
        "unknown to this map" and "already gone" get the same answer, and that
        the other map keeps its node.
        """
        stranger = MindMapRepository.nodes_of(other)[0]

        MindMapService.apply_operations(
            mind_map, [{"type": "node.delete", "uuid": stranger.uuid}]
        )

        assert node_by_uuid(stranger.uuid) is not None
        assert MindMapRepository.node_count(other.id) == 1

    def test_a_foreign_topic_cannot_become_a_parent(self, app, mind_map, root, other):
        """The re-parent path resolves a second UUID, so it needs its own test."""
        stranger = MindMapRepository.nodes_of(other)[0]
        child = add(mind_map, parent=root.uuid, text="Filho")

        with pytest.raises(NotFoundError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.move", "uuid": child, "parent": stranger.uuid}],
            )

        assert node_by_uuid(child).parent_id == root.id

    def test_an_edge_cannot_span_two_maps(self, app, mind_map, root, other):
        stranger = MindMapRepository.nodes_of(other)[0]

        with pytest.raises(NotFoundError):
            MindMapService.apply_operations(
                mind_map,
                [{
                    "type": "edge.create", "uuid": new_id(),
                    "source": root.uuid, "target": stranger.uuid,
                }],
            )

        assert MindMapRepository.edge_count(mind_map.id) == 0

    def test_the_batch_reads_only_its_own_map(self, app, mind_map, other):
        """Cheap to assert, and it is the invariant the four tests above rest on."""
        add(other, text="só do outro")

        loaded = {node.uuid for node in MindMapRepository.nodes_of(mind_map)}
        foreign = {node.uuid for node in MindMapRepository.nodes_of(other)}

        assert loaded.isdisjoint(foreign)


class TestTheApiSurvivesHostileInput:
    """Every field of the operation protocol is attacker-shaped.

    The canvas mints its own identifiers and posts raw JSON, so the batch is
    the widest untrusted surface in the application. What is pinned here is
    not a particular error message: it is that no payload reaches a 500, and
    that nothing lands in the database unvalidated.
    """

    PAYLOADS = [
        pytest.param("'; DROP TABLE mind_maps; --", id="sql"),
        pytest.param("<script>alert(1)</script>", id="script"),
        pytest.param("../../../etc/passwd", id="traversal"),
        pytest.param("javascript:alert(1)", id="js-scheme"),
        pytest.param("\x00\x01\x02", id="control-bytes"),
        pytest.param("{{7*7}}", id="template"),
        pytest.param("𝓊𝓃𝒾𝒸ℴ𝒹ℯ" * 200, id="wide-unicode"),
    ]

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_a_hostile_topic_label_is_stored_as_text(
        self, client, app, mind_map, root, payload
    ):
        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            json={
                "revision": mind_map.revision,
                "operations": [
                    {"type": "node.update", "uuid": root.uuid, "text": payload}
                ],
            },
        )

        assert response.status_code < 500
        if response.status_code == 200:
            stored = node_by_uuid(root.uuid).text
            assert "<script" not in stored.lower()
            assert "\x00" not in stored
            assert len(stored) <= 500

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_a_hostile_url_never_becomes_an_href(
        self, client, app, mind_map, root, payload
    ):
        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            json={
                "revision": mind_map.revision,
                "operations": [
                    {"type": "node.update", "uuid": root.uuid, "url": payload}
                ],
            },
        )

        assert response.status_code < 500
        stored = node_by_uuid(root.uuid).url
        assert stored == "" or stored.split(":", 1)[0] in {"http", "https", "mailto"}

    MALFORMED = [
        pytest.param({"operations": "nao-e-lista"}, id="operations-string"),
        pytest.param({"operations": [None]}, id="operacao-nula"),
        pytest.param({"operations": [{"type": None}]}, id="tipo-nulo"),
        pytest.param({"operations": [{"type": "node.create", "uuid": {"a": 1}}]}, id="uuid-objeto"),
        pytest.param({"operations": [{"type": "node.create", "uuid": ["x"]}]}, id="uuid-lista"),
        pytest.param({"operations": [{"type": "__proto__"}]}, id="proto"),
        pytest.param({"operations": [{"type": "node.update", "uuid": "x" * 5000}]}, id="uuid-enorme"),
        pytest.param({"operations": [{}]}, id="operacao-vazia"),
        pytest.param({"revision": "nao-e-numero", "operations": []}, id="revisao-texto"),
        pytest.param({"revision": [1], "operations": []}, id="revisao-lista"),
        pytest.param({}, id="corpo-vazio"),
    ]

    @pytest.mark.parametrize("body", MALFORMED)
    def test_a_malformed_batch_is_refused_not_crashed(self, client, mind_map, body):
        response = client.post(f"/api/mapas/{mind_map.uuid}/operacoes", json=body)

        assert response.status_code < 500, response.get_data(as_text=True)[:400]
        assert response.is_json

    def test_a_body_that_is_not_json_is_refused(self, client, mind_map):
        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            data="nao e json", content_type="application/json",
        )

        assert response.status_code < 500

    def test_the_batch_ceiling_is_enforced_over_http(self, client, mind_map):
        """Without this an unbounded batch is an unbounded transaction."""
        response = client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            json={
                "operations": [
                    {"type": "node.create", "uuid": new_id(), "parent": None}
                    for _ in range(600)
                ]
            },
        )

        assert response.status_code == 400
        assert MindMapRepository.node_count(mind_map.id) == 1

    @pytest.mark.parametrize(
        "path",
        ["/mapas/{}", "/mapas/{}/markdown", "/mapas/{}/svg", "/api/mapas/{}/grafo"],
    )
    @pytest.mark.parametrize(
        "identifier",
        ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "' OR '1'='1", "%00", "x" * 300],
    )
    def test_a_hostile_identifier_is_a_404_not_a_500(self, client, path, identifier):
        response = client.get(path.format(identifier))

        assert response.status_code in {301, 308, 404, 400}, response.status_code


class TestTheBoardOffersAWayBack:
    """Two affordances the canvas was missing, pinned in the markup.

    Both are behaviour that lives in JavaScript; what is checked here is that
    the pieces it needs are on the page and start in the right state, which is
    the half that silently disappears in a template edit.
    """

    @pytest.fixture()
    def canvas(self, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_the_way_back_exists_and_starts_out_of_the_way(self, canvas):
        """A canvas pans forever, so it can be panned into nothing."""
        button = re.search(r"<button[^>]*data-lost[^>]*>", canvas)
        assert button, "sem botão de voltar ao mapa"
        assert "hidden" in button.group(0), "o botão aparece sem o mapa ter sido perdido"
        assert 'data-action="mm-fit"' in button.group(0)

    def test_the_way_back_is_chrome_not_canvas(self, canvas):
        """Inside the stage, so a press on it must not start a marquee."""
        button = re.search(r"<button[^>]*data-lost[^>]*>", canvas).group(0)
        assert "data-chrome" in button

    def test_arranging_the_board_asks_first(self, canvas):
        """One click used to move every topic on the map."""
        assert 'id="map-organize"' in canvas
        assert 'data-action="mm-organize-confirm"' in canvas

    def test_the_question_names_the_layout_it_will_apply(self, canvas):
        """"Arrumar" means something different in each of the three."""
        assert "data-organize-layout" in canvas

    def test_the_wand_no_longer_arranges_on_its_own(self, canvas):
        """The button opens the question; only the dialog's button acts."""
        wand = re.search(r'<button[^>]*data-action="mm-organize"[^>]*>', canvas)
        assert wand, "botão de arrumar ausente"
        confirm = canvas.index('data-action="mm-organize-confirm"')
        assert confirm > canvas.index('id="map-organize"'), (
            "o botão de confirmar deve viver dentro do diálogo"
        )


class TestTheBoardAnswersTheClickItWasGiven:
    """Three defects that all came from the same place: what a press does.

    * Pressing a topic captured the pointer immediately, and a captured
      pointer retargets the `click` and `dblclick` the browser synthesises to
      the element holding the capture. A double click on a topic therefore
      arrived as a double click on the board: it never opened the editor, it
      created a new topic underneath.
    * Two clicks on the board created a topic - the same two clicks that
      select a word and that open a topic for editing.
    * A topic created for an edit that was then abandoned stayed on the board,
      blank, forever.

    All three live in JavaScript. What is pinned here is the shape of the
    source, which is the part a refactor silently undoes.
    """

    @pytest.fixture()
    def source(self, app):
        from pathlib import Path

        return (
            Path(app.root_path) / "static" / "js" / "modules" / "mindmap" / "interactions.js"
        ).read_text(encoding="utf-8")

    def test_pressing_a_topic_does_not_capture_the_pointer(self, source):
        """The capture belongs to the drag, and there is no drag yet."""
        body = source[source.index("function startDrag"):source.index("function startResize")]
        assert "setPointerCapture" not in body, (
            "a captura no press reancora o dblclick para o quadro"
        )

    def test_the_capture_is_taken_once_the_drag_really_begins(self, source):
        """Past the threshold it has to own the pointer, board or not."""
        move = source[source.index("if (gesture.kind === 'drag')"):]
        threshold = move.index("DRAG_THRESHOLD")
        capture = move.index("setPointerCapture")
        assert capture > threshold, "a captura precisa vir depois do limiar"

    def test_two_clicks_on_the_board_create_nothing(self, source):
        handler = source[source.index("addEventListener('dblclick'"):]
        handler = handler[:handler.index("});")]
        assert "addLoose" not in handler, "dois cliques voltaram a criar um tópico"

    def test_three_clicks_on_the_board_create_one(self, source):
        assert "event.detail !== 3" in source, "o triplo clique deixou de ser o gesto de criar"

    def test_an_abandoned_blank_topic_does_not_survive(self, source):
        assert "abandonEmpty = true" in source, (
            "abandonar uma edição precisa ser o padrão, não a exceção"
        )
        for chain in ("addSibling(uuid)", "addChild(uuid)"):
            before = source[: source.index(chain)]
            assert "abandonEmpty: false" in before[-200:], (
                f"a cadeia do {chain} não pode descartar o tópico que acabou de criar"
            )

    def test_a_brand_new_topic_waits_for_its_element(self, source):
        """Create-then-edit runs before the frame that draws it."""
        assert "requestAnimationFrame(() => beginEdit(" in source
        assert source.count("{ fresh: true }") >= 7, (
            "todo caminho que cria e abre para digitar precisa marcar o tópico como novo"
        )

    def test_the_shortcut_sheet_says_three(self, client, mind_map):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)
        assert "Triplo clique" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="Node não está instalado")
class TestWhereALooseTopicIsBorn:
    """The placement logic, run under Node against fake collaborators.

    Same shape as tests/js/align.test.mjs and run by the same pytest command:
    the module takes four dependencies and none of them has to be real, so the
    arithmetic that decides where a topic lands can be checked without a
    browser. What it pins is the bug that made a board look broken - six
    topics created at one coordinate, stacked, so dragging the top one looked
    like dragging nothing.
    """

    def test_the_javascript_suite_passes(self):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [shutil.which("node"), str(PLACEMENT_SUITE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"


class TestTwoTabsCannotBothWin:
    """The revision is claimed in the database, not decided in Python.

    Comparing the revision a client sent against the loaded object and *then*
    assigning ``revision + 1`` is a read followed by a write. Two tabs that
    both read 4 both wrote 5: each believed it had won, the counter advanced
    once for two edits, and the second silently overwrote the first with the
    fields its own client had been holding - the exact thing the revision
    exists to prevent.

    These run real threads against the file-backed database the fixtures give
    every test, because the failure only exists between two connections.
    """

    def rival(self, app, map_uuid, gate, results, name):
        """One tab: read the revision, wait for the others, then write."""
        def run():
            with app.app_context():
                mind_map = MindMapService.require(map_uuid)
                seen = mind_map.revision
                gate.wait(timeout=10)
                try:
                    applied = MindMapService.apply_operations(
                        mind_map,
                        [{"type": "node.create", "uuid": new_id(),
                          "parent": None, "fields": {"text": name}}],
                        expected_revision=seen,
                    )
                    results[name] = ("aplicado", applied.revision)
                except ConflictError:
                    results[name] = ("conflito", None)
                finally:
                    from app.extensions import db

                    db.session.remove()
        return threading.Thread(target=run)

    def test_only_one_of_two_simultaneous_batches_is_applied(self, app, mind_map):
        gate = threading.Barrier(2)
        results: dict[str, tuple[str, int | None]] = {}
        threads = [self.rival(app, mind_map.uuid, gate, results, name)
                   for name in ("aba-a", "aba-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        applied = [name for name, (outcome, _) in results.items() if outcome == "aplicado"]
        assert len(applied) == 1, f"as duas passaram: {results}"
        assert len(results) == 2, f"uma das abas não terminou: {results}"

    def test_the_counter_advances_once_per_batch_that_lands(self, app, mind_map):
        """Two winners on the same number is how an edit disappears."""
        gate = threading.Barrier(3)
        results: dict[str, tuple[str, int | None]] = {}
        threads = [self.rival(app, mind_map.uuid, gate, results, f"aba-{i}")
                   for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        granted = [revision for _, revision in results.values() if revision is not None]
        assert len(granted) == len(set(granted)), f"revisão concedida duas vezes: {results}"

        with app.app_context():
            after = MindMapService.require(mind_map.uuid)
            assert after.revision == 2, f"o contador andou {after.revision - 1} vez(es)"
            # One batch landed, so exactly one topic joined the root.
            assert MindMapRepository.node_count(after.id) == 2

    def test_a_batch_that_is_refused_writes_nothing(self, app, mind_map, root):
        add(mind_map, parent=root.uuid, text="Primeiro", revision=mind_map.revision)

        with pytest.raises(ConflictError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.create", "uuid": new_id(), "parent": None,
                  "fields": {"text": "Nunca gravado"}}],
                expected_revision=1,
            )

        assert node_by_uuid_text(mind_map, "Nunca gravado") is None

    def test_the_refusal_carries_the_board_as_it_stands(self, app, mind_map, root):
        """The client adopts what comes back, so it has to be the truth."""
        add(mind_map, parent=root.uuid, text="Do outro lado", revision=mind_map.revision)

        with pytest.raises(ConflictError) as refused:
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": root.uuid, "text": "tarde demais"}],
                expected_revision=1,
            )

        graph = refused.value.server_state
        assert graph["revision"] == 2
        assert any(node["text"] == "Do outro lado" for node in graph["nodes"])
        assert all(node["text"] != "tarde demais" for node in graph["nodes"])

    def test_a_batch_without_a_declared_revision_is_still_numbered(self, app, mind_map, root):
        """No guard asked for, so none given - but the counter still moves."""
        before = mind_map.revision
        applied = MindMapService.apply_operations(
            mind_map, [{"type": "node.update", "uuid": root.uuid, "text": "Sem guarda"}]
        )
        assert applied.revision == before + 1

    def test_an_empty_batch_spends_no_revision(self, app, mind_map):
        before = mind_map.revision
        applied = MindMapService.apply_operations(mind_map, [], expected_revision=before)

        assert applied.applied == 0
        assert applied.revision == before
        assert MindMapService.require(mind_map.uuid).revision == before

    def test_tidying_the_board_takes_a_number_of_its_own(self, app, mind_map, root):
        """Two tidies arriving together must not land on the same revision."""
        add(mind_map, parent=root.uuid, text="Ramo")
        before = MindMapService.require(mind_map.uuid).revision

        MindMapService.autolayout(mind_map, "right")

        assert MindMapService.require(mind_map.uuid).revision == before + 1
