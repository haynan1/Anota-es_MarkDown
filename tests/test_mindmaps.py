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

import logging
import pathlib
import re
import json
import shutil
import subprocess
import threading
import uuid as uuid_module
from math import hypot

import pytest

from app.models import MindMap, MindMapNode
from app.models.mind_map import LAYOUT_HINTS, LAYOUT_LABELS, LAYOUTS
from app.repositories.mind_map_repository import MindMapRepository
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.mind_map_export import parse_outline, to_markdown, to_svg
from app.services.mind_map_layout import (
    BRANCH_ROUTING,
    Box,
    LayoutNode,
    bounding_box,
    branch_path,
    branch_routing,
    compute_layout,
    effective_layouts,
)
from app.services.mind_map_service import MAX_DEPTH, MindMapService


JS_DIR = pathlib.Path(__file__).resolve().parent / "js"
PLACEMENT_SUITE = JS_DIR / "mindmap-placement.test.mjs"
BOOT_SUITE = JS_DIR / "mindmap-boot.test.mjs"
ROUTING_SUITE = JS_DIR / "mindmap-routing.test.mjs"


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
        add(mind_map, parent=root.uuid, text="Marketing", color="#22C55E")
        deeper = add(mind_map, parent=root.uuid, text="Engenharia")
        add(mind_map, parent=deeper, text="Plataforma")

        clone = MindMapService.duplicate(mind_map)
        graph = MindMapService.graph_payload(clone)

        assert clone.uuid != mind_map.uuid
        assert len(graph["nodes"]) == 4
        # E a hierarquia inteira veio junto, não só as caixas.
        by_uuid = {node["uuid"]: node for node in graph["nodes"]}
        levels = [node["parent"] for node in graph["nodes"]]
        assert levels.count(None) == 1, "a cópia tem uma raiz só"
        assert any(by_uuid[node["parent"]]["parent"] for node in graph["nodes"] if node["parent"]), (
            "o terceiro nível não sobreviveu à cópia"
        )
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

    def test_the_ceiling_is_published_so_the_board_can_honour_it(self, app, mind_map):
        """A tela precisa do número, não só da recusa.

        Sem ele, o vigésimo Tab seguido montava um lote que o servidor jamais
        aceitaria - e um lote impossível na fila trava a gravação do mapa, não
        só aquele gesto. O teto viaja no grafo e a tela recusa na origem; a
        fronteira exata é fixada dos dois lados, aqui e em
        ``tests/js/mindmap-boot.test.mjs``.
        """
        assert MindMapService.graph_payload(mind_map)["limits"]["depth"] == MAX_DEPTH

    def test_moving_a_tall_branch_is_measured_by_its_own_height(
        self, app, mind_map, root
    ):
        """Não é só o tópico que desce: o ramo inteiro desce com ele.

        A conta - profundidade do novo pai + 1 + altura do que se move - é a
        que a tela repete. Os dois lados da fronteira ficam presos: no
        penúltimo nível o movimento cabe, no seguinte não. Afirmar só a recusa
        deixaria passar um guarda exagerado do lado do navegador, que recusa o
        que este lado aceita.
        """
        chain = [root.uuid]
        for _ in range(MAX_DEPTH - 5):
            chain.append(add(mind_map, parent=chain[-1], text="Nível"))

        def tall_branch():
            """Um ramo solto de três níveis abaixo da própria raiz."""
            top = add(mind_map, text="Galho")
            tip = top
            for _ in range(3):
                tip = add(mind_map, parent=tip, text="Folha")
            return top

        # chain[-1] está a MAX_DEPTH - 5 de profundidade: 15 + 1 + 3 = 19, cabe.
        cabe = tall_branch()
        MindMapService.apply_operations(
            mind_map, [{"type": "node.move", "uuid": cabe, "parent": chain[-1]}]
        )
        assert node_by_uuid(cabe).parent.uuid == chain[-1]

        deeper = add(mind_map, parent=chain[-1], text="Mais um nível")
        nao_cabe = tall_branch()
        with pytest.raises(ValidationError) as excinfo:
            MindMapService.apply_operations(
                mind_map, [{"type": "node.move", "uuid": nao_cabe, "parent": deeper}]
            )

        assert str(MAX_DEPTH) in excinfo.value.message
        assert node_by_uuid(nao_cabe).parent is None

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
    """Conectar é pendurar, e é a única coisa que uma linha quer dizer.

    O quadro desenhava dois tipos de linha - a espinha pai-filho e uma
    associação livre que atravessava o mapa sem mudar nada - e nada na tela
    dizia qual era qual. Qual delas um gesto produzia virou a fonte mais
    confiável de confusão do mapa: "conectar" podia deixar a estrutura
    exatamente como estava. Sobrou uma linha, e ela é a árvore.
    """

    def test_connecting_two_topics_puts_one_inside_the_other(self, app, mind_map, root):
        other = add(mind_map, text="Solto")

        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.move", "uuid": other, "parent": root.uuid}],
        )

        assert node_by_uuid(other).parent_id == node_by_uuid(root.uuid).id

    def test_the_graph_carries_no_second_kind_of_line(self, app, mind_map, root):
        """O payload não tem mais onde guardar uma associação, e é isso que
        garante que nenhuma volte a aparecer por um caminho lateral."""
        payload = MindMapService.graph_payload(mind_map)

        assert "edges" not in payload
        assert "edges" not in payload["limits"]

    def test_the_operation_protocol_refuses_the_old_verbs(self, app, mind_map, root):
        """Uma aba antiga, aberta desde antes, mandaria estas."""
        for verb in ("edge.create", "edge.update", "edge.delete"):
            with pytest.raises(ValidationError):
                MindMapService.apply_operations(
                    mind_map, [{"type": verb, "uuid": new_id()}]
                )

    def test_disconnecting_leaves_the_topic_and_its_branch_standing(
        self, app, mind_map, root
    ):
        branch = add(mind_map, parent=root.uuid, text="Ramo")
        below = add(mind_map, parent=branch, text="Sob o ramo")

        MindMapService.apply_operations(
            mind_map, [{"type": "node.move", "uuid": branch, "parent": None}]
        )

        assert node_by_uuid(branch).parent_id is None
        assert node_by_uuid(below).parent_id == node_by_uuid(branch).id


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


class TestTheTreeGrowsDown:
    """A árvore: a disposição que faltava, e a que o resto do produto sugeria.

    ``right`` and ``down`` were already here, but ``down`` was drawn with the
    sideways curve of a horizontal map - branches leaving the left and right
    faces of nodes whose children sat underneath them. Every arrangement that
    was not ``right`` therefore *looked* like a horizontal map that had been
    shuffled, which is what "só consigo fazer para a direita ou para a
    esquerda" describes. ``tree`` is the arrangement people mean by the word,
    and the routing is what makes it - and ``down``, and ``radial`` - true.
    """

    def make(self, count=2, **sizes):
        width = sizes.get("width", 180.0)
        height = sizes.get("height", 48.0)
        return [
            LayoutNode("raiz", None, width, height),
            *[
                LayoutNode(f"f{index}", "raiz", width, height)
                for index in range(count)
            ],
        ]

    def test_a_child_sits_below_its_parent(self):
        placed = compute_layout(
            [
                LayoutNode("raiz", None, 180, 48),
                LayoutNode("filho", "raiz", 180, 48),
                LayoutNode("neto", "filho", 180, 48),
            ],
            "tree",
        )

        assert placed["filho"][1] > placed["raiz"][1]
        assert placed["neto"][1] > placed["filho"][1]

    def test_a_parent_is_centred_over_its_children(self):
        """What separates an org chart from a list of rows."""
        placed = compute_layout(self.make(count=4), "tree")

        centres = [placed[f"f{index}"][0] + 90 for index in range(4)]
        assert placed["raiz"][0] + 90 == pytest.approx(
            (min(centres) + max(centres)) / 2
        )

    def test_siblings_stand_side_by_side_without_touching(self):
        placed = compute_layout(self.make(count=6), "tree")

        spans = sorted(
            (placed[f"f{index}"][0], placed[f"f{index}"][0] + 180) for index in range(6)
        )
        for (_, right), (left, _) in zip(spans, spans[1:]):
            assert left >= right, "dois irmãos se sobrepõem"

    def test_a_whole_level_shares_one_row(self):
        """The shared bus under a parent only exists because of this: every
        node of a level on one line means every elbow turns at one height."""
        placed = compute_layout(self.make(count=5), "tree")

        rows = {placed[f"f{index}"][1] for index in range(5)}
        assert len(rows) == 1, f"um nível em {len(rows)} alturas"

    def test_a_collapsed_branch_reserves_no_space(self):
        """The promise the other three layouts already make."""
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            LayoutNode("a", "raiz", 180, 48),
            *[LayoutNode(f"a{index}", "a", 180, 48) for index in range(4)],
            LayoutNode("b", "raiz", 180, 48),
        ]
        expanded = compute_layout(nodes, "tree")
        nodes[1] = LayoutNode("a", "raiz", 180, 48, collapsed=True)
        folded = compute_layout(nodes, "tree")

        assert folded["b"][0] < expanded["b"][0]

    def test_a_broken_graph_still_lays_out(self):
        placed = compute_layout(
            [LayoutNode("a", "b", 100, 40), LayoutNode("b", "a", 100, 40)], "tree"
        )
        assert set(placed) == {"a", "b"}

    def test_it_is_offered_everywhere_the_others_are(self):
        assert "tree" in LAYOUTS
        assert LAYOUT_LABELS["tree"] and LAYOUT_HINTS["tree"]
        assert set(LAYOUT_LABELS) == set(LAYOUTS) == set(LAYOUT_HINTS)

    def test_the_settings_form_offers_it(self, client, mind_map):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)
        select = re.search(r'<select[^>]*name="layout".*?</select>', html, re.S)

        assert select, "o formulário do mapa não oferece disposição"
        for value in LAYOUTS:
            assert f'value="{value}"' in select.group(0), value

    def test_organising_a_real_map_stacks_the_child_below(self, app, mind_map, root):
        child = add(mind_map, parent=root.uuid, text="Ramo", x=900, y=0)

        MindMapService.autolayout(mind_map, "tree")

        assert node_by_uuid(child).y > node_by_uuid(root.uuid).y

    def test_the_arrangement_chosen_becomes_the_map_s(self, app, mind_map, root):
        """"Arrumar como árvore" is also "esta é uma árvore" - otherwise the
        next tidy, the next export and the next reload all disagree with what
        is on screen."""
        add(mind_map, parent=root.uuid, text="Ramo")

        MindMapService.autolayout(mind_map, "tree")

        assert mind_map.layout == "tree"
        assert MindMapService.graph_payload(mind_map)["layout"] == "tree"

    def test_an_unknown_arrangement_keeps_the_one_the_map_has(
        self, app, mind_map, root
    ):
        """A forged request must not be able to park a map on a layout that
        nothing knows how to draw."""
        add(mind_map, parent=root.uuid, text="Ramo")
        MindMapService.autolayout(mind_map, "tree")

        MindMapService.autolayout(mind_map, "arvore-de-natal")

        assert mind_map.layout == "tree"

    def test_the_route_refuses_it_the_same_way(self, client, mind_map, root):
        add(mind_map, parent=root.uuid, text="Ramo")

        response = client.post(
            f"/api/mapas/{mind_map.uuid}/organizar",
            json={"layout": {"nested": "árvore"}},
        )

        assert response.status_code == 200
        assert response.get_json()["graph"]["layout"] in LAYOUTS


class TestALinkFollowsTheArrangement:
    """A ligação não é enfeite sobre a disposição - é a mesma decisão.

    Every layout here computed sensible coordinates and then handed them to
    one connector: the cubic that leaves a node's right face and arrives at
    the next one's left. On a vertical map that draws branches sideways out of
    a parent whose children are underneath it; on a radial one it draws an
    S-curve where the point is the angle.
    """

    def test_every_arrangement_has_a_routing(self):
        assert set(BRANCH_ROUTING) == set(LAYOUTS)
        assert len(set(BRANCH_ROUTING.values())) == len(LAYOUTS), (
            "duas disposições desenhando igual seriam uma disposição só"
        )

    def test_an_unknown_arrangement_still_draws(self):
        """A map saved before a layout existed, or a hand-edited row."""
        assert branch_routing("nao-existe") == "horizontal"

    def test_the_vertical_curve_leaves_the_bottom_face(self):
        path = branch_path("vertical", Box(0, 0, 180, 48), Box(240, 124, 180, 48))

        assert path.startswith("M90.0,48.0"), path
        assert path.endswith("330.0,124.0"), path

    def test_the_elbow_turns_halfway_between_the_rows(self):
        path = branch_path("elbow", Box(0, 0, 180, 48), Box(240, 124, 180, 48))

        assert path.startswith("M90.0,48.0")
        assert path.endswith("L330.0,124.0")
        assert "86.0" in path, "a dobra fica no meio dos 48 aos 124"

    def test_siblings_share_one_horizontal_run(self):
        parent = Box(0, 0, 180, 48)
        left = branch_path("elbow", parent, Box(-300, 124, 180, 48))
        right = branch_path("elbow", parent, Box(300, 124, 180, 48))

        def turn(path):
            return re.findall(r"-?\d+\.\d+", path)[3]

        assert turn(left) == turn(right)

    def test_an_only_child_gets_a_straight_drop(self):
        path = branch_path("elbow", Box(0, 0, 180, 48), Box(0, 124, 180, 48))

        assert "Q" not in path, "um filho único não merece uma curva"
        assert set(re.findall(r"-?\d+\.\d+,", path)) == {"90.0,"}

    def test_the_elbow_reverses_when_the_child_was_dragged_above(self):
        """Hand-dragged nodes are the normal state of a board, and a link that
        crosses the box it comes out of is how a tidy map turns into a knot."""
        path = branch_path("elbow", Box(0, 300, 180, 48), Box(0, 0, 180, 48))

        assert path.startswith("M90.0,300.0"), path
        assert path.endswith("90.0,48.0"), path

    def test_a_spoke_starts_and_ends_on_the_boxes(self):
        parent, child = Box(0, 0, 180, 48), Box(0, 400, 180, 48)
        path = branch_path("spoke", parent, child)

        assert path == "M90.0,48.0 L90.0,400.0"

    def test_a_spoke_between_two_stacked_nodes_is_not_a_division_by_zero(self):
        assert branch_path("spoke", Box(0, 0, 10, 10), Box(0, 0, 10, 10))

    def test_no_path_ever_carries_negative_zero(self):
        """`-0.0` in a `d` is valid SVG and a smell in a diff - and it is the
        one value the two implementations round differently."""
        paths = [
            branch_path(routing, Box(-90, -24, 180, 48), Box(-90, 100, 180, 48))
            for routing in set(BRANCH_ROUTING.values())
        ]

        for path in paths:
            assert "-0.0" not in path, path


class TestTheExportDrawsTheSameMap:
    """O SVG e a tela são o mesmo desenho, não dois que quase concordam."""

    def branch_paths(self, svg):
        return [
            match
            for match in re.findall(r'<path d="([^"]+)" stroke="[^"]+" stroke-width="2"', svg)
        ]

    def test_a_tree_is_exported_with_elbows(self, app, mind_map, root):
        add(mind_map, parent=root.uuid, text="Ramo")
        MindMapService.autolayout(mind_map, "tree")

        svg = to_svg(mind_map, MindMapRepository.nodes_of(mind_map))

        paths = self.branch_paths(svg)
        assert paths, "o SVG saiu sem ligações"
        assert all("C" not in path for path in paths), paths

    def test_the_same_map_exports_differently_under_each_arrangement(
        self, app, mind_map, root
    ):
        add(mind_map, parent=root.uuid, text="Ramo")
        nodes = MindMapRepository.nodes_of(mind_map)

        drawings = set()
        for layout in LAYOUTS:
            mind_map.layout = layout
            drawings.add(tuple(self.branch_paths(to_svg(mind_map, nodes))))

        assert len(drawings) == len(LAYOUTS), (
            "duas disposições exportando o mesmo desenho"
        )

    def test_the_exported_path_is_the_one_the_canvas_would_draw(
        self, app, mind_map, root
    ):
        child = add(mind_map, parent=root.uuid, text="Ramo")
        MindMapService.autolayout(mind_map, "tree")

        nodes = {node.uuid: node for node in MindMapRepository.nodes_of(mind_map)}
        svg = to_svg(mind_map, list(nodes.values()))

        parent, kid = nodes[root.uuid], nodes[child]
        expected = branch_path(
            "elbow",
            Box(parent.x, parent.y, parent.width, parent.height),
            Box(kid.x, kid.y, kid.width, kid.height),
        )
        assert f'd="{expected}"' in svg


@pytest.mark.skipif(shutil.which("node") is None, reason="Node não está instalado")
class TestBothLanguagesDrawTheSameLine:
    """O contrato entre os dois desenhistas.

    The canvas redraws a link on every frame of a drag, so the geometry has to
    exist in the browser; the SVG export has to produce the identical drawing,
    so it exists in Python too. That is a duplication, and the honest way to
    hold a duplication is to make something read both: the Node suite emits
    its whole case table with the paths it produced, and this recomputes every
    one of them here. Change either side alone and this fails, naming the case.
    """

    def table(self):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [shutil.which("node"), str(ROUTING_SUITE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
        return json.loads(result.stdout)

    def test_every_branch_is_drawn_identically(self):
        for case in self.table()["branches"]:
            here = branch_path(
                case["routing"], Box(**case["parent"]), Box(**case["child"])
            )
            assert here == case["d"], (
                f"{case['routing']}: python {here!r} contra javascript {case['d']!r}"
            )

    def test_both_sides_map_a_layout_to_the_same_routing(self):
        for layout, routing in self.table()["routings"].items():
            assert branch_routing(layout) == routing, layout


class TestTheBoardFacesTheWayItGrows:
    """As alças de ligação ficam nas faces por onde o mapa cresce.

    A node offered a port on its left and right whatever the arrangement. On a
    tree that is an invitation to draw the map the wrong way round: the two
    handles a person can grab point at the two directions nothing on that
    board lives in. The layout decides which faces are the live ones, the
    board carries the answer as one attribute, and the CSS follows it.

    Read from the files rather than from a browser, the way the other
    invariants in this suite are: what is pinned is that the attribute is
    written, that all four ports exist to be shown, and that each orientation
    hides exactly the pair it should.
    """

    @pytest.fixture()
    def canvas_css(self, app):
        return (
            pathlib.Path(app.root_path) / "static" / "css" / "mindmap.css"
        ).read_text(encoding="utf-8")

    @pytest.fixture()
    def canvas_js(self, app):
        return (
            pathlib.Path(app.root_path)
            / "static"
            / "js"
            / "modules"
            / "mindmap"
            / "canvas.js"
        ).read_text(encoding="utf-8")

    def test_a_node_carries_all_four_ports(self, canvas_js):
        """Built once. Building only the two in use would mean rebuilding
        every node when the layout changes - the one moment the board must
        not flicker."""
        assert "['left', 'right', 'top', 'bottom']" in canvas_js

    def test_the_board_announces_which_way_it_faces(self, client, app, mind_map):
        for layout, expected in (
            ("right", "horizontal"),
            ("down", "vertical"),
            ("tree", "vertical"),
            ("radial", "radial"),
        ):
            MindMapService.update(mind_map, layout=layout)
            html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)
            board = re.search(r"<main[^>]*data-mind-map[^>]*>", html).group(0)
            assert f'data-orientation="{expected}"' in board, layout

    def test_each_node_carries_its_own_branch_s_orientation(self, canvas_js):
        """Per node, not per board.

        A board that mixes arrangements has the organogram's topics and the
        fan's topics on one screen, and they do not offer the same faces. One
        attribute on the page could only ever describe one of them.
        """
        assert "element.dataset.orientation = orientation(store.arrangementOf(node))" in canvas_js
        assert "isVertical(arrangement)" in canvas_js

    def test_each_orientation_shows_one_pair_of_ports(self, canvas_css):
        rules = canvas_css
        assert '.mm-port[data-side="top"],\n.mm-port[data-side="bottom"] { display: none; }' in rules, (
            "sem a regra base, um mapa horizontal mostra quatro alças"
        )
        assert (
            '.mm-node[data-orientation="vertical"] .mm-port[data-side="left"]' in rules
            and '.mm-node[data-orientation="vertical"] .mm-port[data-side="right"] { display: none; }'
            in rules
        ), "um ramo vertical ainda oferece as alças laterais"
        assert '.mm-node[data-orientation="radial"] .mm-port[data-side="top"]' in rules

    def test_a_folded_branch_offers_no_port_on_either_growing_face(self, canvas_css):
        """And the rule comes last, so it wins the tie against the ones above
        it - equal specificity is decided by order, and the folded node has to
        win."""
        collapsed = canvas_css.index('.mm-node[data-collapsed="true"] .mm-port')
        oriented = canvas_css.rindex('.mm-node[data-orientation="radial"] .mm-port')
        assert collapsed > oriented, "a regra do ramo fechado precisa vir depois"
        rule = canvas_css[collapsed : collapsed + 200]
        assert 'data-side="right"' in rule and 'data-side="bottom"' in rule


class TestOneBoardHoldsEveryKind:
    """Um ramo pode ter disposição própria - e o mapa continua sendo um mapa.

    The four arrangements were a property of the map, so choosing one meant
    giving up the other three. They are now a property of a *branch*: a node
    can name its own, everything under it is arranged that way, and the map
    goes on arranging everything that named nothing. An organogram hanging off
    a radial fan hanging off a horizontal spine is one board.

    What the composition has to promise is exactly what a single arrangement
    already promised: nothing overlaps, a folded branch reserves no space,
    every node gets a place, and a branch that named nothing still follows the
    map when the map changes.
    """

    def spine(self, **branches):
        """A root with one child per entry, each carrying two leaves."""
        nodes = [LayoutNode("raiz", None, 180, 48)]
        for name, arrangement in branches.items():
            nodes.append(LayoutNode(name, "raiz", 180, 48, layout=arrangement))
            nodes.extend(
                LayoutNode(f"{name}{index}", name, 180, 48) for index in range(2)
            )
        return nodes

    def box(self, placed, keys, size=(180, 48)):
        xs = [placed[key][0] for key in keys]
        ys = [placed[key][1] for key in keys]
        return (min(xs), min(ys), max(xs) + size[0], max(ys) + size[1])

    def test_a_branch_is_arranged_by_what_it_names(self):
        placed = compute_layout(self.spine(a="tree", b=None), "right")

        # The tree branch grows down out of its own node...
        assert placed["a0"][1] > placed["a"][1]
        assert placed["a0"][1] == placed["a1"][1], "um nível da árvore em duas alturas"
        # ...while the branch that named nothing still grows to the right.
        assert placed["b0"][0] > placed["b"][0]
        assert placed["b0"][1] != placed["b1"][1]

    def test_naming_the_arrangement_it_already_had_changes_nothing(self):
        """A branch split off for no reason is a branch that stops sharing
        its siblings' column - so saying "right" on a right map must be the
        same as saying nothing at all."""
        silent = compute_layout(self.spine(a=None, b=None), "right")
        stated = compute_layout(self.spine(a="right", b=None), "right")

        assert stated == silent

    def test_the_map_still_moves_everything_that_named_nothing(self):
        nodes = self.spine(a="tree", b=None)
        across = compute_layout(nodes, "right")
        down = compute_layout(nodes, "down")

        assert down["b"] != across["b"], "o ramo sem opinião ignorou o mapa"
        assert down["b0"] != across["b0"]

    def test_a_named_branch_ignores_the_map_changing(self):
        """Relative to its own node, which is the part that is its own: where
        the block lands is still the map's decision."""
        nodes = self.spine(a="tree", b=None)

        shapes = []
        for direction in ("right", "down", "radial"):
            placed = compute_layout(nodes, direction)
            origin = placed["a"]
            shapes.append(
                tuple(
                    (round(placed[key][0] - origin[0], 3), round(placed[key][1] - origin[1], 3))
                    for key in ("a0", "a1")
                )
            )

        assert len(set(shapes)) == 1, f"a árvore mudou de forma com o mapa: {shapes}"

    def test_a_named_branch_keeps_its_place_among_its_siblings(self):
        """The defect this pins, found by the collapsed test above and fixed
        before it shipped: the composition collected a region's own nodes
        first and appended the differently-arranged blocks afterwards, so a
        branch that named an arrangement silently jumped to the end of its
        row. Every arrangement here promises siblings in the order the writer
        put them, and a branch with an opinion is still one of the siblings.
        """
        plain = compute_layout(self.spine(a=None, b=None, c=None), "right")
        mixed = compute_layout(self.spine(a=None, b="tree", c=None), "right")

        def order(placed):
            return sorted(("a", "b", "c"), key=lambda key: placed[key][1])

        assert order(plain) == ["a", "b", "c"]
        assert order(mixed) == ["a", "b", "c"], (
            "o ramo com disposição própria saiu do lugar entre os irmãos"
        )

    def test_two_differently_arranged_branches_do_not_overlap(self):
        placed = compute_layout(self.spine(a="tree", b="radial", c=None), "right")

        blocks = [
            self.box(placed, ["a", "a0", "a1"]),
            self.box(placed, ["b", "b0", "b1"]),
            self.box(placed, ["c", "c0", "c1"]),
        ]
        for first, second in ((0, 1), (0, 2), (1, 2)):
            left, right = blocks[first], blocks[second]
            apart = (
                left[2] <= right[0] or right[2] <= left[0]
                or left[3] <= right[1] or right[3] <= left[1]
            )
            assert apart, f"os blocos {first} e {second} se sobrepõem: {left} {right}"

    def test_arrangements_nest_as_deep_as_the_map_does(self):
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            LayoutNode("a", "raiz", 180, 48, layout="tree"),
            LayoutNode("a1", "a", 180, 48, layout="radial"),
            *[LayoutNode(f"a1{index}", "a1", 180, 48) for index in range(4)],
            LayoutNode("a2", "a", 180, 48),
        ]
        placed = compute_layout(nodes, "right")

        assert set(placed) == {n.key for n in nodes}
        # The tree level still shares a row - measured on the *block*, which
        # is what the row holds. The radial node itself sits in the middle of
        # its own fan, which is exactly the point of composing them.
        block_top = min(placed[f"a1{index}"][1] for index in range(4))
        block_top = min(block_top, placed["a1"][1])
        assert block_top == placed["a2"][1]
        # ...and the radial branch below it fans out around its own node
        # rather than lining up with anything above.
        centre = (placed["a1"][0] + 90, placed["a1"][1] + 24)
        radii = {
            round(hypot(placed[f"a1{index}"][0] + 90 - centre[0],
                        placed[f"a1{index}"][1] + 24 - centre[1]), 3)
            for index in range(4)
        }
        assert len(radii) == 1, f"o leque não é um leque: {radii}"

    def test_a_folded_branch_reserves_no_space_even_when_it_names_its_own(self):
        open_nodes = self.spine(a="radial", b=None)
        shut_nodes = [
            LayoutNode(n.key, n.parent, n.width, n.height,
                       collapsed=(n.key == "a"), layout=n.layout)
            for n in open_nodes
        ]

        opened = compute_layout(open_nodes, "right")
        shut = compute_layout(shut_nodes, "right")

        assert shut["b"] != opened["b"]
        assert "a0" not in shut, "um ramo fechado não recebe coordenada"

    def test_every_node_gets_a_place(self):
        for direction in LAYOUTS:
            placed = compute_layout(
                self.spine(a="tree", b="radial", c="down", d=None), direction
            )
            assert len(placed) == 13, direction

    def test_a_broken_graph_with_mixed_arrangements_still_lays_out(self):
        placed = compute_layout(
            [
                LayoutNode("a", "b", 100, 40, layout="tree"),
                LayoutNode("b", "a", 100, 40, layout="radial"),
            ],
            "right",
        )
        assert set(placed) == {"a", "b"}

    def test_an_arrangement_nothing_can_draw_is_ignored(self):
        """A hand-edited row must not park a branch on a name with no
        geometry behind it."""
        honest = compute_layout(self.spine(a=None), "right")
        forged = compute_layout(self.spine(a="espiral"), "right")

        assert forged == honest


class TestWhatABranchInherits:
    """A regra de herança, que três lugares aplicam e um teste compara."""

    def tree_of(self, nodes):
        from app.services.mind_map_layout import build_tree

        return build_tree(nodes), {node.key: node for node in nodes}

    def test_a_branch_that_names_nothing_takes_what_is_above_it(self):
        nodes = [
            LayoutNode("raiz", None, 180, 48),
            LayoutNode("a", "raiz", 180, 48, layout="tree"),
            LayoutNode("a1", "a", 180, 48),
            LayoutNode("a2", "a1", 180, 48, layout="radial"),
            LayoutNode("a3", "a2", 180, 48),
            LayoutNode("b", "raiz", 180, 48),
        ]
        tree, by_key = self.tree_of(nodes)

        resolved = effective_layouts(tree, by_key, "right")

        assert resolved == {
            "raiz": "right",
            "a": "tree",
            "a1": "tree",
            "a2": "radial",
            "a3": "radial",
            "b": "right",
        }

    def test_a_folded_branch_still_has_an_answer(self):
        """It is not laid out, but it is still exported, still read by the
        outline, and still there the moment it is opened."""
        nodes = [
            LayoutNode("raiz", None, 180, 48, collapsed=True, layout="tree"),
            LayoutNode("a", "raiz", 180, 48),
        ]
        tree, by_key = self.tree_of(nodes)

        assert effective_layouts(tree, by_key, "right")["a"] == "tree"

    def test_the_exporter_inherits_exactly_the_same_way(self, app, mind_map, root):
        """Two walks of one rule.

        The layout resolves inheritance over its own nodes and the drawing
        resolves it over database rows, because neither can use the other's
        input. Pinned against each other here, over one tree, so a change that
        reaches only one of them fails rather than producing an export drawn
        unlike its board.
        """
        from app.services.mind_map_drawing import (
            effective_arrangements,
            index_nodes,
        )

        branch = add(mind_map, parent=root.uuid, text="Ramo", layout="tree")
        inner = add(mind_map, parent=branch, text="Dentro")
        add(mind_map, parent=inner, text="Fundo", layout="radial")
        add(mind_map, parent=root.uuid, text="Outro")
        MindMapService.update(mind_map, layout="down")

        nodes = MindMapRepository.nodes_of(mind_map)
        by_id = {node.id: node.uuid for node in nodes}
        from_export = {
            by_id[identifier]: value
            for identifier, value in effective_arrangements(
                mind_map, nodes, index_nodes(nodes)
            ).items()
        }

        from app.services.mind_map_layout import build_tree

        layout_nodes = [
            LayoutNode(
                node.uuid,
                by_id.get(node.parent_id) if node.parent_id else None,
                node.width,
                node.height,
                node.is_collapsed,
                node.layout,
            )
            for node in nodes
        ]
        from_layout = effective_layouts(
            build_tree(layout_nodes),
            {n.key: n for n in layout_nodes},
            mind_map.layout,
        )

        assert from_export == from_layout
        assert set(from_export.values()) == {"down", "tree", "radial"}


class TestABranchArrangementIsStored:
    """O que a tela manda, o servidor guarda - ou recusa."""

    def test_it_survives_a_round_trip(self, app, mind_map, root):
        branch = add(mind_map, parent=root.uuid, text="Ramo", layout="tree")

        payload = MindMapService.graph_payload(mind_map)
        stored = next(n for n in payload["nodes"] if n["uuid"] == branch)

        assert stored["layout"] == "tree"
        assert node_by_uuid(branch).layout == "tree"

    def test_a_node_that_named_nothing_answers_with_an_empty_string(self, app):
        """Not ``null``: the canvas compares this field against the server's
        copy on every save, and a select whose "same as the map" option
        carried ``null`` would compare unequal to its own empty string
        forever, resending the node on every batch."""
        mind_map = MindMapService.create("Vazio", "")
        payload = MindMapService.graph_payload(mind_map)

        assert payload["nodes"][0]["layout"] == ""

    def test_it_can_be_handed_back_to_the_map(self, app, mind_map, root):
        branch = add(mind_map, parent=root.uuid, text="Ramo", layout="radial")

        MindMapService.apply_operations(
            mind_map,
            [{"type": "node.update", "uuid": branch, "fields": {"layout": ""}}],
        )

        assert node_by_uuid(branch).layout is None

    def test_an_arrangement_nothing_can_draw_is_refused(self, app, mind_map, root):
        branch = add(mind_map, parent=root.uuid, text="Ramo")

        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.update", "uuid": branch, "fields": {"layout": "espiral"}}],
            )

    def test_a_hostile_type_is_refused_rather_than_stored(self, app, mind_map, root):
        branch = add(mind_map, parent=root.uuid, text="Ramo")

        for hostile in ({"a": 1}, ["tree"], 7, True):
            with pytest.raises(ValidationError):
                MindMapService.apply_operations(
                    mind_map,
                    [{"type": "node.update", "uuid": branch,
                      "fields": {"layout": hostile}}],
                )
        assert node_by_uuid(branch).layout is None

    def test_organising_a_mixed_map_moves_every_branch_its_own_way(
        self, app, mind_map, root
    ):
        branch = add(mind_map, parent=root.uuid, text="Árvore", layout="tree")
        below = add(mind_map, parent=branch, text="Sob a árvore")
        plain = add(mind_map, parent=root.uuid, text="Comum")
        beside = add(mind_map, parent=plain, text="Ao lado")

        MindMapService.autolayout(mind_map, "right")

        assert node_by_uuid(below).y > node_by_uuid(branch).y, "a árvore não desceu"
        assert node_by_uuid(beside).x > node_by_uuid(plain).x, "o comum não foi ao lado"


class TestTheBranchArrangementIsOffered:
    """A opção existe onde a pergunta aparece."""

    @pytest.fixture()
    def canvas(self, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_the_panel_offers_every_arrangement_and_inheriting(self, canvas):
        select = re.search(
            r"<select[^>]*data-inspector-layout.*?</select>", canvas, re.S
        )
        assert select, "o painel do tópico não oferece disposição de ramo"

        markup = select.group(0)
        assert '<option value="">' in markup, "falta a opção de seguir o mapa"
        for value in LAYOUTS:
            assert f'value="{value}"' in markup, value

    def test_the_panel_says_what_the_branch_resolves_to(self, canvas):
        """"Como o mapa" is an empty answer, and on a mixed board an empty
        answer is ambiguous - from the map, or from a branch three levels up?

        And it is read out with the control rather than left as loose text
        beside it: it carries half the answer, so a screen reader that never
        reaches it never gets that half.
        """
        select = re.search(
            r"<select[^>]*data-inspector-layout[^>]*>", canvas, re.S
        ).group(0)
        hint = re.search(r'<p[^>]*data-branch-layout-hint[^>]*>', canvas).group(0)

        assert 'aria-describedby="mm-branch-layout-hint"' in select
        assert 'id="mm-branch-layout-hint"' in hint

    def test_arranging_warns_about_the_branches_that_will_not_follow(self, canvas):
        """Otherwise "arrumar como árvore" moves the map, leaves two branches
        exactly where they were, and says nothing about why."""
        note = re.search(r"<p[^>]*data-mixed-note.*?</p>", canvas, re.S)

        assert note, "o diálogo não avisa sobre ramos com disposição própria"
        assert "hidden" in note.group(0), "o aviso aparece num mapa sem ramos próprios"
        assert 'data-action="mm-clear-branch-layouts"' in note.group(0), (
            "o aviso precisa levar de volta"
        )


class TestALinkCanBeCut:
    """Soltar um bloco em cima do outro pendura um no outro - e dava para
    desfazer de nenhum jeito.

    A linha entre pai e filho já era desenhada com um caminho de acerto gordo
    e ``cursor: pointer``, ou seja, anunciava-se clicável; mas a marca que o
    clique procura estava só no ``<g>`` em volta, e o que o ponteiro acerta é
    o caminho lá dentro. Resultado: a linha prometia um clique que não
    respondia. O gesto que a cria tinha ida e não tinha volta.

    Um segundo defeito no mesmo lugar, que só apareceu ao consertar o
    primeiro: ``.mm-link.is-selected`` existia no CSS e nada no JavaScript
    aplicava a classe - nem a conexão livre, que já era clicável, mostrava que
    estava selecionada.

    Lido dos arquivos, como as outras invariantes desta suíte.
    """

    @pytest.fixture()
    def canvas(self, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    @pytest.fixture()
    def source(self, app):
        base = pathlib.Path(app.root_path) / "static" / "js"
        return {
            name: (base / "modules" / "mindmap" / f"{name}.js").read_text(encoding="utf-8")
            for name in ("canvas", "interactions", "inspector", "actions")
        }

    def test_the_mark_the_click_looks_for_is_on_what_the_pointer_hits(self, source):
        """On the paths, not only on the group around them."""
        assert "entry.hit.dataset.branch = node.uuid" in source["canvas"]
        assert "entry.path.dataset.branch = node.uuid" in source["canvas"]

    def test_a_press_on_a_hierarchy_line_reaches_someone(self, source):
        assert "event.target.closest('[data-branch]')" in source["interactions"]
        assert "onSelectBranch(branchHit.dataset.branch)" in source["interactions"]

    def test_the_delete_key_takes_the_selected_line_first(self, source):
        """With a line selected there is no selected topic, so the fallback
        would delete nothing and the key would look broken."""
        interactions = source["interactions"]
        removal = interactions.index("if (onRemoveLink && onRemoveLink()) break;")
        fallback = interactions.index("actions.removeSelection();", removal)
        assert removal < fallback

    def test_a_selected_connection_is_visible_as_selected(self, source):
        """`.mm-link.is-selected` was in the stylesheet and nothing applied
        it - not even to the association, which was already clickable."""
        assert "classList.toggle('is-selected'" in source["canvas"]
        assert "setLinkSelection" in source["canvas"]

    def test_cutting_is_reparenting_to_nowhere(self, source):
        assert "function detach(uuid)" in source["actions"]
        assert "return reparent(uuid, null);" in source["actions"]

    def test_the_line_offers_exactly_one_thing(self, canvas):
        panel = re.search(r'<div class="mm-inspector-form" data-branch-form.*?\n        </div>',
                          canvas, re.S)
        assert panel, "não há painel para a ligação de hierarquia"

        markup = panel.group(0)
        assert 'data-action="mm-detach"' in markup
        assert "mm-branch-demote" not in markup, "não há segundo tipo de linha"
        assert "data-branch-child" in markup and "data-branch-parent" in markup, (
            "o painel precisa dizer que ligação é essa"
        )
        assert "hidden" in markup, "o painel aparece sem linha selecionada"

    def test_cutting_is_also_reachable_without_a_pointer(self, canvas):
        """A line takes no focus, so with the button only on the line this
        would be a change only a mouse could make."""
        actions = re.search(
            r'<div class="mm-inspector-actions">(?:(?!</div>).)*?data-action="mm-add-child".*?</div>',
            canvas, re.S
        )
        assert actions, "painel do tópico sem barra de ações"
        assert 'data-action="mm-detach"' in actions.group(0)

    def test_the_promise_the_panel_makes_is_the_one_the_code_keeps(self, source):
        """The panel says nothing is deleted. `detach` moves one field and
        touches no other node - the branch underneath comes along."""
        assert "actions.removeSelection" not in source["inspector"].split(
            "case 'mm-detach'"
        )[1].split("break;")[0]


class TestALooseTopicIsNotTheCentre:
    """Cortar uma linha deixava dois "blocos fortes" na tela, sem volta.

    Two defects with one cause: the canvas answered "is this the centre of the
    map?" with "does it have a parent?". Cut one line and the branch below it
    took the map's accent, its white text and its shadow - announcing itself
    as the subject of the board rather than as the branch that had come
    loose. And the gesture that made it had no inverse within reach: dragging
    the whole branch onto another topic was the only way back, so the button
    people found instead was "Conectar a…", which draws an association - a
    dashed line that looks resolved and restores no hierarchy at all.
    """

    @pytest.fixture()
    def canvas(self, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    @pytest.fixture()
    def source(self, app):
        base = pathlib.Path(app.root_path) / "static" / "js" / "modules" / "mindmap"
        return {
            name: (base / f"{name}.js").read_text(encoding="utf-8")
            for name in ("canvas", "interactions", "minimap")
        }

    @pytest.fixture()
    def canvas_css(self, app):
        return (
            pathlib.Path(app.root_path) / "static" / "css" / "mindmap.css"
        ).read_text(encoding="utf-8")

    def test_the_centre_is_one_topic_not_every_orphan(self, source):
        """`!node.parent` answers "is this a root", which is not the same
        question."""
        canvas = source["canvas"]
        assert "roots[0].uuid === node.uuid" in canvas
        assert "element.dataset.root = centre ? 'true' : 'false';" in canvas
        assert "element.dataset.root = node.parent ? 'false' : 'true';" not in canvas

    def test_the_accent_goes_to_the_centre_only(self, source):
        assert "shown.color || (centre ? accent : '')" in source["canvas"]

    def test_the_minimap_answers_the_same_question(self, source):
        """Three accent dots in the miniature would say there are three maps."""
        assert "roots[0].uuid === node.uuid" in source["minimap"]
        assert "if (!node.parent) rect.dataset.root" not in source["minimap"]

    def test_a_loose_topic_says_so_quietly(self, source, canvas_css):
        assert "element.dataset.loose = loose ? 'true' : 'false';" in source["canvas"]
        assert '.mm-node[data-loose="true"] { border-style: dashed; }' in canvas_css

    def test_hanging_a_topic_back_is_reachable_without_dragging(self, source, canvas):
        interactions = source["interactions"]
        assert "function beginAttach(uuid, mode = 'move')" in interactions
        assert "actions.reparent(child, uuid)" in interactions, (
            "pendurar precisa devolver hierarquia, não criar uma associação"
        )
        assert 'data-action="mm-attach"' in canvas

    def test_the_two_halves_of_the_gesture_sit_together(self, canvas):
        """Cutting and re-hanging are one decision seen from two sides."""
        actions = re.search(
            r'<div class="mm-inspector-actions">(?:(?!</div>).)*?data-action="mm-add-child".*?</div>',
            canvas, re.S
        ).group(0)

        assert 'data-action="mm-detach"' in actions
        assert 'data-action="mm-attach"' in actions

    def test_a_half_made_attachment_is_always_cancellable(self, source):
        """Senão o próximo clique em qualquer lugar mexe na estrutura.

        Todo caminho que abandona o gesto o encerra: o Esc, o clique no vazio
        e a troca de ferramenta. A única exceção é trocar *para* a ferramenta
        que é o gesto - o que não é abandoná-lo, é escolhê-lo.
        """
        interactions = source["interactions"]

        assert "clearConnect();\n    clearAttach();" in interactions, "no vazio"
        assert "clearConnect();\n        clearAttach();" in interactions, "no Esc"
        assert "if (next !== 'share') clearAttach();" in interactions, (
            "trocar de ferramenta encerra o gesto, menos ao escolher a dele"
        )


class TestTheOutlineShowsAndTheBoardEdits:
    """A Estrutura mostra a forma do mapa; quem a muda é a tela.

    Duas superfícies editando a mesma estrutura era pedir para alguém
    arrastar num lugar e procurar o resultado no outro. O painel ficou com o
    que é leitura - abrir e fechar um ramo, encontrar um tópico na tela - e a
    tela ficou com a edição, que é onde ela já estava.

    O que não podia se perder junto é o teclado: a tela é uma figura, e uma
    figura é inalcançável por leitor de tela. Por isso Alt e as setas mudam a
    hierarquia do tópico selecionado, no palco.
    """

    @pytest.fixture()
    def canvas(self, client, mind_map):
        response = client.get(f"/mapas/{mind_map.uuid}")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    @pytest.fixture()
    def source(self, app):
        base = pathlib.Path(app.root_path) / "static" / "js" / "modules" / "mindmap"
        return {
            name: (base / f"{name}.js").read_text(encoding="utf-8")
            for name in ("inspector", "interactions", "actions")
        }

    def test_the_panel_no_longer_rearranges(self, source):
        inspector = source["inspector"]
        for gone in ("dragstart", "dropZone", "dropAllowed", "actions.indent"):
            assert gone not in inspector, gone

    def test_but_it_still_folds_and_finds(self, source):
        inspector = source["inspector"]
        assert "actions.toggleCollapse(uuid)" in inspector
        assert "context.onReveal(uuid)" in inspector
        assert "event.key === 'Enter' || event.key === ' '" in inspector

    def test_the_board_took_the_keyboard_that_restructures(self, source):
        """Sem isto não sobraria caminho nenhum de teclado para mudar a
        hierarquia, e o painel acessível teria virado só um espelho."""
        interactions = source["interactions"]
        assert "event.altKey && !event.shiftKey" in interactions
        assert "actions.indent(primary)" in interactions
        assert "actions.outdent(primary)" in interactions
        assert "actions.shiftSibling(primary, -1)" in interactions

    def test_the_shortcut_sheet_says_so(self, canvas):
        sheet = re.search(r'<dl class="mm-shortcuts">.*?</dl>', canvas, re.S).group(0)
        assert "Alt" in sheet and "hierarquia" in sheet
        assert "Na Estrutura" not in sheet, "o painel não reorganiza mais"

    def test_the_empty_panel_is_drawn(self, source):
        """Um painel em branco não diz se o mapa está vazio ou se o painel
        quebrou. Todo estado é desenhado, inclusive este."""
        assert "mm-outline-empty" in source["inspector"]
        assert "Ainda não há tópicos" in source["inspector"]

    def test_the_panel_says_what_it_is(self, canvas):
        hint = re.search(r'<p class="mm-outline-hint">.*?</p>', canvas, re.S).group(0)
        assert "A forma do mapa" in hint
        assert "Arraste" not in hint


class TestASharedTopic:
    """Um tópico que vale para várias etapas.

    O caso de verdade: "Objetivo de campanha" tem seis filhos - Vendas, Leads,
    Tráfego, App, Alcance, Visitas - e "Modelo de alcance", com os seis modelos
    de campanha dentro, estava pendurado só em Vendas. Ele vale para todas.

    Uma árvore não sabe dizer isso: um tópico tem um pai. O espelho é como
    passa a ser dito sem que o mapa deixe de ser uma árvore - uma linha como
    qualquer outra, e na ponta dela um tópico que mora noutro lugar. É o mesmo
    tópico, não uma cópia, e é essa diferença que estes testes guardam: seis
    cópias divergem no dia em que alguém corrige uma; um tópico compartilhado
    não tem como divergir de si mesmo.
    """

    @pytest.fixture()
    def other(self, app):
        return MindMapService.create("Outro mapa", "")

    @pytest.fixture()
    def stages(self, app, mind_map, root):
        """Vendas e Leads, com o bloco comum pendurado em Vendas."""
        vendas = add(mind_map, parent=root.uuid, text="Vendas")
        leads = add(mind_map, parent=root.uuid, text="Leads")
        bloco = add(mind_map, parent=vendas, text="Modelo de alcance")
        add(mind_map, parent=bloco, text="Shopping")
        add(mind_map, parent=bloco, text="Performance MAX")
        return vendas, leads, bloco

    def share(self, mind_map, parent, target):
        identifier = new_id()
        MindMapService.apply_operations(
            MindMapService.require(mind_map.uuid),
            [{"type": "node.create", "uuid": identifier, "parent": parent,
              "fields": {"mirror_of": target}}],
        )
        return identifier

    def test_the_same_topic_appears_under_another_stage(self, app, mind_map, stages):
        _vendas, leads, bloco = stages

        mirror = self.share(mind_map, leads, bloco)

        assert node_by_uuid(mirror).mirror_of_id == node_by_uuid(bloco).id
        payload = MindMapService.graph_payload(MindMapService.require(mind_map.uuid))
        shown = next(n for n in payload["nodes"] if n["uuid"] == mirror)
        assert shown["mirror_of"] == bloco

    def test_renaming_the_original_renames_every_appearance(self, app, mind_map, stages):
        """É o mesmo tópico. Seis cópias divergiriam no dia em que alguém
        corrigisse uma; esta é a diferença que o espelho compra."""
        _vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        MindMapService.apply_operations(
            MindMapService.require(mind_map.uuid),
            [{"type": "node.update", "uuid": bloco, "fields": {"text": "Modelos"}}],
        )

        # O espelho não guarda texto nenhum: ele mostra o de lá.
        assert node_by_uuid(mirror).text == ""
        assert node_by_uuid(bloco).text == "Modelos"

    def test_the_board_shows_one_box_and_a_line_from_each_place(
        self, app, mind_map, stages
    ):
        """Sete caixas com o mesmo nome dizem menos do que uma caixa com sete
        linhas chegando nela.

        A primeira tentativa repetia a caixa embaixo de cada etapa, e o quadro
        virou uma fileira de rótulos idênticos - "muitos mecanismos
        repetidos". Uma segunda aparição deixou de ocupar lugar no arranjo: o
        que a desenha é a linha que sai do pai dela e chega no tópico de
        verdade.
        """
        _vendas, leads, bloco = stages
        self.share(mind_map, leads, bloco)

        nodes = MindMapRepository.nodes_of(MindMapService.require(mind_map.uuid))
        by_id = {node.id: node.uuid for node in nodes}
        placed = compute_layout(
            [
                LayoutNode(
                    node.uuid,
                    by_id.get(node.parent_id) if node.parent_id else None,
                    node.width, node.height, node.is_collapsed, node.layout,
                    by_id.get(node.mirror_of_id) if node.mirror_of_id else None,
                )
                for node in nodes
            ],
            "tree",
        )

        espelhos = [n.uuid for n in nodes if n.mirror_of_id]
        assert espelhos, "o teste precisa de um tópico compartilhado"
        assert not any(uuid in placed for uuid in espelhos), (
            "uma segunda aparição não ocupa lugar: ela é uma linha, não uma caixa"
        )
        assert bloco in placed, "o tópico de verdade continua desenhado"

    def board_with(self, blocks):
        """Um mapa de seis etapas com vários blocos compartilhados.

        `blocks` é ``{nome: etapas que o compartilham}``. Cada bloco leva três
        subtópicos, para que o ramo dele tenha largura de verdade.
        """
        nodes = [LayoutNode("raiz", None, 180, 48)]
        etapas = [f"etapa{i}" for i in range(6)]
        nodes += [LayoutNode(e, "raiz", 180, 48) for e in etapas]
        for bloco, compartilhado in blocks.items():
            nodes.append(LayoutNode(bloco, etapas[0], 180, 48))
            nodes += [LayoutNode(f"{bloco}{i}", bloco, 180, 48) for i in range(3)]
            nodes += [
                LayoutNode(f"s-{bloco}-{e}", e, 180, 48, mirror_of=bloco)
                for e in compartilhado
            ]
        nodes.append(LayoutNode("comum", etapas[5], 180, 48))
        return nodes

    def overlaps(self, nodes, placed):
        from itertools import combinations

        por_key = {n.key: n for n in nodes}
        caixas = {
            key: (x, y, x + por_key[key].width, y + por_key[key].height)
            for key, (x, y) in placed.items()
        }
        return [
            (a, b) for (a, um), (b, dois) in combinations(caixas.items(), 2)
            if um[0] < dois[2] and dois[0] < um[2] and um[1] < dois[3] and dois[1] < um[3]
        ]

    def test_arranging_a_map_with_several_shared_topics_never_overlaps(self, app):
        """O defeito que isto fixa: "arrumar" bagunçava o fluxo inteiro.

        A primeira versão puxava cada compartilhado para o meio dos pais dele,
        um de cada vez e sem reservar lugar - então dois deles caíam um sobre
        o outro, e sobre quem estivesse embaixo. Seis sobreposições em cada
        arranjo, num mapa de dezoito caixas.

        Agora eles saem do fluxo e vão para uma faixa no pé do mapa, lado a
        lado. Um lugar reservado é a única forma de não haver choque.
        """
        nodes = self.board_with({"modelo": [f"etapa{i}" for i in range(1, 6)],
                                 "publico": [f"etapa{i}" for i in range(2, 5)]})

        for arranjo in LAYOUTS:
            placed = compute_layout(nodes, arranjo)
            assert not self.overlaps(nodes, placed), (
                f"{arranjo}: {self.overlaps(nodes, placed)[:3]}"
            )

    def test_the_shared_band_keeps_each_branch_together(self, app):
        """Um bloco compartilhado é um mapinha, e continua parecendo um: o
        ramo dele viaja junto e fica abaixo dele."""
        nodes = self.board_with({"modelo": ["etapa1", "etapa2"],
                                 "publico": ["etapa3", "etapa4"]})
        placed = compute_layout(nodes, "tree")

        for bloco in ("modelo", "publico"):
            assert all(
                placed[f"{bloco}{i}"][1] > placed[bloco][1] for i in range(3)
            ), bloco

        # E os dois blocos ficam na mesma faixa, não empilhados.
        assert placed["modelo"][1] == placed["publico"][1]

    def test_the_band_follows_the_axis_the_map_grows_on(self, app):
        """Uma faixa horizontal debaixo de um mapa horizontal largava o bloco
        *abaixo e à esquerda* dos próprios pais, com o ramo dele apontando de
        volta para cima - dois eixos brigando no mesmo desenho.

        A profundidade cresce numa direção só, e um tópico compartilhado é
        mais fundo do que todos os pais dele.
        """
        nodes = self.board_with({"modelo": ["etapa1", "etapa2", "etapa3"]})

        across = compute_layout(nodes, "right")
        pais = [across[f"etapa{i}"] for i in range(1, 4)]
        assert across["modelo"][0] > max(x for x, _ in pais), (
            "num mapa que cresce para o lado, a faixa é uma coluna à direita"
        )
        assert across["modelo0"][0] > across["modelo"][0], (
            "e o ramo do bloco continua crescendo para o mesmo lado"
        )

        for arranjo in ("tree", "down"):
            placed = compute_layout(nodes, arranjo)
            pais = [placed[f"etapa{i}"] for i in range(1, 4)]
            assert placed["modelo"][1] > max(y for _, y in pais), arranjo
            assert placed["modelo0"][1] > placed["modelo"][1], arranjo

    def test_the_band_keeps_the_rhythm_of_the_arrangement(self, app):
        """A árvore usa um vão menor entre níveis do que os outros arranjos.

        A faixa usar o vão padrão quebrava a cadência do mapa justamente na
        última linha, que é onde ela mais aparece - três níveis a 124px e o
        quarto a 144.
        """
        nodes = self.board_with({"modelo": ["etapa1", "etapa2"]})
        placed = compute_layout(nodes, "tree")

        linhas = sorted({round(y) for _, y in placed.values()})
        vaos = {b - a for a, b in zip(linhas, linhas[1:])}
        assert len(vaos) == 1, f"a cadeia de níveis ficou irregular: {sorted(vaos)}"

    def test_a_deep_branch_elsewhere_does_not_push_the_band_away(self, app):
        """A faixa se afasta do mais fundo que *cruza com ela*, e não do mais
        fundo do mapa: um ramo comprido do outro lado do quadro não tem por
        que empurrar as linhas compartilhadas para longe de quem as chama."""
        nodes = self.board_with({"modelo": ["etapa4", "etapa5"]})
        raso = compute_layout(nodes, "tree")["modelo"][1]

        # Um ramo fundo, bem longe do lado em que a faixa cai.
        fundo = list(nodes)
        anterior = "etapa0"
        for nivel in range(4):
            fundo.append(LayoutNode(f"fundo{nivel}", anterior, 180, 48))
            anterior = f"fundo{nivel}"

        assert compute_layout(fundo, "tree")["modelo"][1] == raso

    def test_the_shared_topic_sits_between_the_places_that_share_it(self, app, mind_map, stages):
        """Um tópico com um pai fica onde a árvore o pôs. Um que vale para
        várias etapas não tem *um* lugar certo - tem vários - então desce para
        o meio deles, que é onde as linhas se cruzam menos."""
        vendas, leads, bloco = stages
        self.share(mind_map, leads, bloco)
        MindMapService.autolayout(MindMapService.require(mind_map.uuid), "tree")

        nodes = {n.uuid: n for n in MindMapRepository.nodes_of(
            MindMapService.require(mind_map.uuid))}
        alvo, um, dois = nodes[bloco], nodes[vendas], nodes[leads]

        centro = ((um.x + um.width / 2) + (dois.x + dois.width / 2)) / 2
        assert abs((alvo.x + alvo.width / 2) - centro) < 1
        assert alvo.y > max(um.y + um.height, dois.y + dois.height)

    def test_duplicating_a_map_keeps_its_shared_topics(self, app, mind_map, stages):
        """Uma cópia que apontasse para o mapa original seriam dois mapas se
        editando mutuamente, que não é o que "duplicar" quer dizer."""
        _vendas, leads, bloco = stages
        self.share(mind_map, leads, bloco)

        clone = MindMapService.duplicate(MindMapService.require(mind_map.uuid))
        nodes = MindMapRepository.nodes_of(clone)
        dentro = {n.id for n in nodes}
        espelhos = [n for n in nodes if n.mirror_of_id]

        assert len(espelhos) == 1, "a cópia perdeu o tópico compartilhado"
        assert all(n.mirror_of_id in dentro for n in espelhos), (
            "a cópia aponta para o mapa original"
        )

    def test_a_mirror_never_grows_a_branch_of_its_own(self, app, mind_map, stages):
        """Aceitar filhos aqui criaria dois lugares onde o mesmo assunto
        continua de formas diferentes - o custo exato de duplicar."""
        _vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        with pytest.raises(ValidationError, match="compartilhado"):
            MindMapService.apply_operations(
                MindMapService.require(mind_map.uuid),
                [{"type": "node.create", "uuid": new_id(), "parent": mirror,
                  "fields": {"text": "Um subtópico"}}],
            )

    def test_nor_by_moving_something_into_it(self, app, mind_map, stages):
        vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        with pytest.raises(ValidationError, match="compartilhado"):
            MindMapService.apply_operations(
                MindMapService.require(mind_map.uuid),
                [{"type": "node.move", "uuid": vendas, "parent": mirror}],
            )

    def test_a_topic_that_already_has_a_branch_cannot_become_a_mirror(
        self, app, mind_map, stages
    ):
        """O ramo é do original. Um tópico com ramo virando espelho perderia
        o dele de vista sem apagá-lo - dois assuntos no mesmo lugar."""
        _vendas, leads, bloco = stages

        with pytest.raises(ValidationError, match="subtópicos"):
            MindMapService.apply_operations(
                MindMapService.require(mind_map.uuid),
                [{"type": "node.update", "uuid": bloco, "fields": {"mirror_of": leads}}],
            )

    def test_a_mirror_of_itself_is_refused(self, app, mind_map, stages):
        _vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        with pytest.raises(ValidationError):
            MindMapService.apply_operations(
                MindMapService.require(mind_map.uuid),
                [{"type": "node.update", "uuid": mirror, "fields": {"mirror_of": mirror}}],
            )

    def test_a_mirror_of_a_mirror_points_at_the_real_topic(self, app, mind_map, stages):
        """Uma cadeia a resolver a cada desenho é uma cadeia que um dia fica
        longa. Dois espelhos do mesmo tópico são dois espelhos, não uma fila."""
        vendas, leads, bloco = stages
        first = self.share(mind_map, leads, bloco)
        second = self.share(mind_map, vendas, first)

        assert node_by_uuid(second).mirror_of_id == node_by_uuid(bloco).id

    def test_removing_an_appearance_leaves_the_topic_alone(self, app, mind_map, stages):
        _vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        MindMapService.apply_operations(
            MindMapService.require(mind_map.uuid),
            [{"type": "node.delete", "uuid": mirror}],
        )

        assert node_by_uuid(mirror) is None
        assert node_by_uuid(bloco) is not None
        assert len(MindMapService.graph_payload(
            MindMapService.require(mind_map.uuid)
        )["nodes"]) == 6

    def test_deleting_the_topic_takes_its_appearances(self, app, mind_map, stages):
        """Uma referência a algo que não existe mais não é nada."""
        _vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        MindMapService.apply_operations(
            MindMapService.require(mind_map.uuid),
            [{"type": "node.delete", "uuid": bloco}],
        )

        assert node_by_uuid(mirror) is None

    def test_the_markdown_writes_it_once_and_refers_to_it(self, app, mind_map, stages):
        """Repetir o ramo em cada etapa daria um arquivo que se contradiz
        sozinho, e perderia justamente o que o espelho carrega: é o mesmo."""
        _vendas, leads, bloco = stages
        self.share(mind_map, leads, bloco)

        text = to_markdown(
            MindMapService.require(mind_map.uuid),
            MindMapRepository.nodes_of(MindMapService.require(mind_map.uuid)),
        )

        assert text.count("Shopping") == 1, "o ramo saiu duas vezes"
        assert text.count("Modelo de alcance") == 2
        assert "o mesmo tópico, ver acima" in text

    def test_the_drawing_shows_one_box_and_two_lines(self, app, mind_map, stages):
        """A figura exportada e o quadro na tela dizem a mesma coisa.

        E o que os dois dizem é uma caixa só. Repetir a caixa embaixo de cada
        etapa foi a primeira tentativa, e sete caixas com o mesmo nome dizem
        menos do que uma caixa com sete linhas chegando nela.
        """
        _vendas, leads, bloco = stages
        self.share(mind_map, leads, bloco)

        svg = to_svg(
            MindMapService.require(mind_map.uuid),
            MindMapRepository.nodes_of(MindMapService.require(mind_map.uuid)),
        )

        assert svg.count("Modelo de alcance") == 1, "a caixa saiu repetida"
        assert 'stroke-dasharray="6 5"' in svg, (
            "o segundo caminho até um tópico precisa se distinguir do primeiro"
        )

    def test_the_gesture_is_reachable_from_the_board(self, client, mind_map, app):
        """O defeito que isto fixa: a ação existia só dentro do painel do
        tópico, e o painel começa fechado.

        Funcionava - o teste de serviço passava, o clique programático criava
        o espelho - e mesmo assim ninguém conseguia usar, porque não havia
        como chegar até ela. Uma ação atrás de um painel fechado é uma ação
        que não existe. Agora ela está na barra de ferramentas, ao lado das
        outras três, com tecla própria.
        """
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)

        barra = re.search(r'<div class="mm-toolbar".*?</div>', html, re.S)
        assert barra, "sem barra de ferramentas"
        assert 'data-tool-button="share"' in barra.group(0), (
            "a ação precisa estar onde as outras ferramentas estão"
        )
        assert "(S)" in barra.group(0), "e anunciar a própria tecla"

        interactions = (
            pathlib.Path(app.root_path)
            / "static" / "js" / "modules" / "mindmap" / "interactions.js"
        ).read_text(encoding="utf-8")
        assert "if (tool === 'share')" in interactions
        assert "setTool('share')" in interactions, "a tecla S liga a ferramenta"
        assert "if (tool === 'share') setTool('select')" in interactions, (
            "terminar devolve a seleção, senão o próximo clique recomeça sozinho"
        )

    def test_moving_a_topic_says_where_the_other_gesture_is(self, app):
        """O engano que este aviso corta: "conectei numa e desconectou da
        outra".

        Conectar move - é o que uma árvore quer dizer - e quem queria o mesmo
        bloco valendo para seis etapas descobre isso uma etapa de cada vez,
        sem nada na tela dizendo que existe outro caminho. O aviso aparece no
        instante do engano e carrega as duas saídas: desfazer, e a ferramenta
        que faz o que a pessoa queria.
        """
        interactions = (
            pathlib.Path(app.root_path)
            / "static" / "js" / "modules" / "mindmap" / "interactions.js"
        ).read_text(encoding="utf-8")

        assert "function noteItMoved(uuid, from)" in interactions
        assert "Ctrl+Z" in interactions and "(S)" in interactions, (
            "o aviso precisa carregar as duas saídas"
        )
        # Nos três caminhos em que uma pessoa "conecta".
        assert interactions.count("noteItMoved(") == 4, (
            "o arraste, o Conectar a… e a ferramenta Conectar, mais a definição"
        )
        # E só quando algo de fato saiu de algum lugar.
        corpo = interactions[interactions.index("function noteItMoved"):]
        assert "if (!from) return;" in corpo[:400], (
            "um tópico solto que ganha um pai não saiu de lugar nenhum"
        )

    def test_sharing_stays_open_for_the_next_place(self, app):
        """Um bloco que vale para uma etapa quase sempre vale para as outras.

        "Modelo de alcance" existe embaixo de seis, e fechar o modo a cada
        clique obrigaria a percorrer o mesmo caminho seis vezes para dizer uma
        coisa só.
        """
        interactions = (
            pathlib.Path(app.root_path)
            / "static" / "js" / "modules" / "mindmap" / "interactions.js"
        ).read_text(encoding="utf-8")

        passo = interactions[interactions.index("function attachStep(uuid)"):]
        passo = passo[: passo.index("\n  }\n")]

        assert "if (attachMode === 'share')" in passo
        assert passo.index("attachMode === 'share'") < passo.index("clearAttach()"), (
            "compartilhar não pode passar pelo encerramento do modo"
        )
        assert "shared += 1" in passo and "Esc para terminar" in passo

    def test_it_is_offered_where_a_topic_is_edited(self, client, mind_map):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)

        assert 'data-action="mm-share"' in html
        panel = re.search(
            r'<div class="mm-inspector-form" data-mirror-form.*?\n        </div>',
            html, re.S
        )
        assert panel, "sem painel para o tópico compartilhado"
        assert "não uma cópia" in panel.group(0) or "editar num lugar muda em todos" in panel.group(0), (
            "o painel precisa dizer que editar num lugar muda em todos"
        )
        assert 'data-action="mm-goto-original"' in panel.group(0)

    def test_a_forged_mirror_cannot_reach_another_map(self, app, mind_map, root, other):
        outsider = add(other, text="De outro mapa")

        with pytest.raises((ValidationError, NotFoundError)):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.create", "uuid": new_id(), "parent": root.uuid,
                  "fields": {"mirror_of": outsider}}],
            )

    def test_hostile_shapes_never_reach_the_column(self, app, mind_map, stages):
        _vendas, leads, bloco = stages
        mirror = self.share(mind_map, leads, bloco)

        for hostile in ({"a": 1}, ["x"], 7, True, "../../etc"):
            with pytest.raises((ValidationError, NotFoundError)):
                MindMapService.apply_operations(
                    MindMapService.require(mind_map.uuid),
                    [{"type": "node.update", "uuid": mirror,
                      "fields": {"mirror_of": hostile}}],
                )
        assert node_by_uuid(mirror).mirror_of_id == node_by_uuid(bloco).id


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

        drawing = to_svg(mind_map, MindMapRepository.nodes_of(mind_map))
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


class TestTheNameAndColourAreEditable:
    """O nome e a cor predominante do mapa, e o caminho até eles.

    O defeito que isto prende não é "não dá para editar" - dava. É o defeito
    anterior a esse: o lápis ao lado do título era ``opacity: 0`` até o hover,
    então a única coisa que dizia que o nome e a cor se editam só existia para
    quem já tinha levado o ponteiro exatamente ali. Num toque não há hover
    nenhum, e pelo teclado o ícone aparecia depois de o foco já ter chegado.

    Uma capacidade que ninguém encontra é uma capacidade que não existe, então
    o que se afirma aqui é sobre *alcance* tanto quanto sobre efeito: o lápis
    aparece em repouso, a mesma ação está escrita por extenso no menu, e a cor
    vem da paleta curada em vez do seletor cru do sistema.
    """

    def test_the_name_and_the_colour_change_together(self, app, client, mind_map):
        response = client.post(
            f"/mapas/{mind_map.uuid}/editar",
            data={
                "title": "Outro nome",
                "description": mind_map.description,
                "color": "#EF4444",
                "layout": mind_map.layout,
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert mind_map.title == "Outro nome"
        assert mind_map.color == "#EF4444"

    def test_the_address_follows_the_name(self, app, mind_map):
        MindMapService.update(mind_map, title="Nome completamente outro")
        assert mind_map.slug == "nome-completamente-outro"

    def test_the_colour_reaches_the_board_and_the_exports(self, app, mind_map, root):
        """A cor predominante não é enfeite: ela pinta o tópico central, o
        traço de cada ramo e sai assim em toda figura exportada."""
        MindMapService.update(mind_map, color="#14B8A6")

        assert MindMapService.graph_payload(mind_map)["color"] == "#14B8A6"
        assert "#14B8A6" in MindMapService.export_svg(mind_map)

    def test_a_colour_that_is_not_a_colour_is_refused(self, app, client, mind_map):
        before = mind_map.color
        response = client.post(
            f"/mapas/{mind_map.uuid}/editar",
            data={"title": "Nome", "color": "javascript:alert(1)", "layout": "right"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert mind_map.color == before

    def test_an_empty_name_is_refused(self, app, client, mind_map):
        before = mind_map.title
        client.post(
            f"/mapas/{mind_map.uuid}/editar",
            data={"title": "   ", "color": mind_map.color, "layout": "right"},
        )
        assert mind_map.title == before

    # ── O caminho até o controle ────────────────────────────────────────────

    def test_the_pencil_is_visible_without_hovering(self):
        """A regra é de CSS, então é no CSS que ela é conferida."""
        rules = pathlib.Path("app/static/css/mindmap.css").read_text(encoding="utf-8")
        opacity = re.search(r"\.mm-title-pencil \{ opacity: ([0-9.]+)", rules)

        assert opacity, "a regra do lápis sumiu"
        assert float(opacity.group(1)) > 0, (
            "o lápis voltou a ser invisível em repouso: a única affordance de "
            "editar nome e cor deixaria de existir para toque e teclado"
        )

    def test_the_same_action_is_written_out_in_the_menu(self, app, client, mind_map):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)

        assert "Nome e cor do mapa" in html
        # E aponta para o mesmo diálogo que o lápis abre - uma ação, um lugar.
        assert html.count('data-target="map-settings"') == 2

    def test_the_dialog_offers_the_curated_palette_and_a_free_picker(
        self, app, client, mind_map
    ):
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)
        offered = re.findall(r'data-map-swatch="(#[0-9A-Fa-f]{6})"', html)

        from app.blueprints.mindmaps.routes import NODE_PALETTE

        assert offered == [value for value, _label in NODE_PALETTE]
        # O seletor livre continua, e é ele que guarda o valor: é o campo do
        # formulário, funciona sem JavaScript, e atende a cor de marca que não
        # está entre as oito.
        assert 'class="color-input"' in html
        assert f'value="{mind_map.color}"' in html

    def test_the_dialog_opens_on_the_colour_the_map_actually_has(self, app, client):
        created = MindMapService.create("Com cor", color="#EC4899")
        html = client.get(f"/mapas/{created.uuid}").get_data(as_text=True)

        assert 'value="#EC4899"' in html

    # ── E o cadeado ─────────────────────────────────────────────────────────

    def test_a_locked_map_offers_neither_path(self, app, client, mind_map):
        MindMapService.toggle_lock(mind_map)
        html = client.get(f"/mapas/{mind_map.uuid}").get_data(as_text=True)

        # O lápis vira cadeado, e o item de menu fica desabilitado de verdade.
        assert "mm-title-lock" in html
        assert "mm-title-pencil" not in html
        assert 'data-target="map-settings"\n                  disabled' in html

    def test_a_locked_map_refuses_the_post_and_keeps_both(self, app, client, mind_map):
        MindMapService.toggle_lock(mind_map)
        title, colour = mind_map.title, mind_map.color

        client.post(
            f"/mapas/{mind_map.uuid}/editar",
            data={"title": "Nome proibido", "color": "#000000", "layout": "right"},
        )

        assert (mind_map.title, mind_map.color) == (title, colour)


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

    def test_a_topic_cannot_be_hung_under_another_map(self, app, mind_map, root, other):
        """A fronteira que a aresta livre atravessava continua fechada para a
        única linha que sobrou."""
        outsider = add(other, text="De outro mapa")

        with pytest.raises((ValidationError, NotFoundError)):
            MindMapService.apply_operations(
                mind_map,
                [{"type": "node.move", "uuid": outsider, "parent": root.uuid}],
            )

        assert node_by_uuid(outsider).map_id == other.id

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


class TestARefusalLeavesATrace:
    """Uma recusa da API tem de aparecer no log da aplicação.

    O defeito que isto prende não foi de código, foi de diagnóstico. A tela
    entrou num laço reenviando um lote impossível - cinco ``POST /operacoes``
    devolvendo 400 em quinze segundos - e o log da aplicação não tinha uma
    linha sequer sobre isso. A única evidência disponível era uma captura de
    tela do log de acesso: os números, sem o motivo.

    ``ServiceError`` é sempre uma recusa nossa, escrita por nós, e nunca o
    caminho feliz. Registrá-la custa uma linha rara e devolve a diferença
    entre investigar e adivinhar.
    """

    @pytest.fixture()
    def mirror(self, app, mind_map, root):
        """Um tópico compartilhado - o lugar onde nada pode ser pendurado."""
        original = add(mind_map, parent=root.uuid, text="Modelo de alcance")
        outra_etapa = add(mind_map, parent=root.uuid, text="Leads")
        return add(mind_map, parent=outra_etapa, mirror_of=original)

    def refused_batch(self, client, mind_map, mirror):
        """Um subtópico dentro de um espelho: recusado, sempre."""
        return client.post(
            f"/api/mapas/{mind_map.uuid}/operacoes",
            json={
                "revision": MindMapService.require(mind_map.uuid).revision,
                "operations": [
                    {
                        "type": "node.create",
                        "uuid": new_id(),
                        "parent": mirror,
                        "fields": {"text": "Impossível"},
                    }
                ],
            },
        )

    def test_the_reason_reaches_the_log(self, app, client, caplog, mind_map, mirror):
        with caplog.at_level(logging.WARNING, logger="app.errors"):
            response = self.refused_batch(client, mind_map, mirror)

        assert response.status_code == 400
        assert caplog.records, "a recusa não deixou rastro nenhum"

        line = caplog.records[-1].getMessage()
        # O suficiente para achar o pedido no log de acesso e saber o porquê
        # sem ter de reproduzir nada.
        assert "POST" in line
        assert f"/api/mapas/{mind_map.uuid}/operacoes" in line
        assert "400" in line
        assert "compartilhado" in line

    def test_it_is_a_warning_and_not_an_error(self, app, client, caplog, mind_map, mirror):
        """Nada quebrou: o pedido foi entendido e respondido.

        Um ERROR aqui treinaria quem opera a ignorar ERROR, que é o único
        nível em que uma falha de verdade tem para aparecer.
        """
        with caplog.at_level(logging.WARNING, logger="app.errors"):
            self.refused_batch(client, mind_map, mirror)

        assert [record.levelname for record in caplog.records] == ["WARNING"]

    def test_a_request_that_succeeds_stays_quiet(self, app, client, caplog, mind_map, root):
        with caplog.at_level(logging.WARNING, logger="app.errors"):
            response = client.post(
                f"/api/mapas/{mind_map.uuid}/operacoes",
                json={
                    "revision": mind_map.revision,
                    "operations": [
                        {
                            "type": "node.create",
                            "uuid": new_id(),
                            "parent": root.uuid,
                            "fields": {"text": "Possível"},
                        }
                    ],
                },
            )

        assert response.status_code == 200
        assert not caplog.records


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

    def test_the_question_offers_every_arrangement(self, canvas):
        """"Arrumar" means something different in each of the four.

        It used to only *name* the one already chosen, and the choosing
        happened in a select inside a different dialog. A setting two dialogs
        away is a setting nobody finds: the board could be arranged as a tree
        since the day it shipped, and it read as a board that only grew
        sideways.
        """
        picker = re.search(
            r"<fieldset[^>]*data-layout-picker.*?</fieldset>", canvas, re.S
        )
        assert picker, "o diálogo de arrumar não oferece a disposição"

        markup = picker.group(0)
        for value in LAYOUTS:
            assert f'value="{value}"' in markup, value
            assert LAYOUT_LABELS[value] in markup, value
            assert LAYOUT_HINTS[value] in markup, value
        assert markup.count("checked") == 1, (
            "exatamente uma disposição começa marcada - a do mapa"
        )
        assert "<legend" in markup, "o grupo de rádios precisa de nome"

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

    def test_the_board_comes_up(self, app, tmp_path, mind_map, root):
        """Um mapa recém-criado abre com os tópicos na tela.

        Dois defeitos desta classe chegaram ao produto na mesma sessão, e os
        dois deixavam a área de trabalho em branco: uma remoção grande levou
        junto código vizinho - uma função de ícone, depois as declarações do
        índice de filhos - e o primeiro desenho estourou. O laço de render
        morre junto, e não sobra nada na tela.

        A suíte roda com os módulos de verdade e um dublê só para o DOM, e é
        este teste que lhe entrega o grafo que o servidor realmente produz -
        assim nem o formato do payload pode divergir do que a tela espera.
        """
        add(mind_map, parent=root.uuid, text="Um ramo")
        graph = tmp_path / "grafo.json"
        graph.write_text(
            json.dumps(MindMapService.graph_payload(mind_map)), encoding="utf-8"
        )

        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [shutil.which("node"), str(BOOT_SUITE), str(graph)],
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"

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
