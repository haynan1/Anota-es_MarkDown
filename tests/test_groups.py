"""Grupos: reunir documentos do mesmo assunto, na ordem que fizer sentido.

The rules that matter, and are pinned here: a group never owns a document (it
can be deleted without deleting anything), membership is idempotent, and the
order is a property of the membership rather than of the document - the same
document can be first in one group and last in another.
"""

from __future__ import annotations

import pytest

from app.models import Group, document_groups
from app.repositories.document_repository import DocumentQuery, DocumentRepository
from app.repositories.group_repository import GroupRepository
from app.services.exceptions import NotFoundError, ValidationError
from app.services.group_service import GroupService


@pytest.fixture()
def group(app):
    return GroupService.create("Manual do produto", "Tudo sobre a versão 2.")


@pytest.fixture()
def three_documents(make_document):
    return [make_document(title=f"Capítulo {index}") for index in range(1, 4)]


class TestLifecycle:
    def test_a_group_is_created_with_a_slug_and_a_uuid(self, app):
        created = GroupService.create("Proposta Acme", "Contrato e anexos", "#22C55E")

        assert created.slug == "proposta-acme"
        assert created.uuid
        assert created.description == "Contrato e anexos"
        assert created.color == "#22C55E"

    def test_two_groups_cannot_share_a_name(self, app, group):
        with pytest.raises(ValidationError) as excinfo:
            GroupService.create("manual do produto")

        assert "Já existe" in excinfo.value.message

    def test_a_nameless_group_is_refused(self, app):
        with pytest.raises(ValidationError):
            GroupService.create("   ")

    def test_markup_in_a_name_is_stripped(self, app):
        created = GroupService.create("<script>alert(1)</script>Relatórios")

        assert "<script>" not in created.name
        assert "Relatórios" in created.name

    def test_an_invalid_colour_falls_back_to_the_default(self, app):
        created = GroupService.create("Cores", color="javascript:alert(1)")
        assert created.color == "#4F46E5"

    def test_an_enormous_name_is_cut_to_the_column_width(self, app):
        from app.models.group import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH

        created = GroupService.create("N" * 5000, description="D" * 5000)

        assert len(created.name) <= MAX_NAME_LENGTH
        assert len(created.description) <= MAX_DESCRIPTION_LENGTH
        assert len(created.slug) <= 110

    def test_the_group_limit_is_enforced(self, app, monkeypatch):
        """A runaway import must not be able to create groups without end."""
        from app.services import group_service

        monkeypatch.setattr(group_service, "MAX_GROUPS", 2)
        GroupService.create("Um")
        GroupService.create("Dois")

        with pytest.raises(ValidationError) as excinfo:
            GroupService.create("Três")

        assert "Limite" in excinfo.value.message

    def test_a_group_page_is_not_an_injection_surface(self, client, app):
        """The name is echoed on three screens; it must arrive escaped."""
        created = GroupService.create('Aspas " e <b>tags</b>')

        body = client.get(f"/grupos/{created.uuid}").data.decode()

        assert "<b>tags</b>" not in body
        assert "&lt;b&gt;" in body or "tags" in body

    def test_renaming_refreshes_the_slug(self, app, group):
        GroupService.update(group, name="Manual do produto v3")
        assert group.slug == "manual-do-produto-v3"

    def test_deleting_a_group_keeps_its_documents(self, app, db, group, three_documents):
        GroupService.add_documents(group, three_documents)

        GroupService.delete(group)

        assert db.session.scalars(db.select(Group)).all() == []
        assert len(DocumentRepository.get_many_by_uuids(
            [document.uuid for document in three_documents]
        )) == 3

    def test_an_unknown_group_is_a_not_found(self, app):
        with pytest.raises(NotFoundError):
            GroupService.require("00000000-0000-0000-0000-000000000000")


class TestMembership:
    def test_documents_are_added_in_order(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)

        titles = [d.display_title for d in GroupRepository.documents_of(group)]
        assert titles == ["Capítulo 1", "Capítulo 2", "Capítulo 3"]

    def test_adding_twice_changes_nothing(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)
        added = GroupService.add_documents(group, three_documents)

        assert added == 0
        assert len(GroupRepository.documents_of(group)) == 3

    def test_a_document_can_belong_to_several_groups(self, app, three_documents):
        first = GroupService.create("Assunto A")
        second = GroupService.create("Assunto B")
        document = three_documents[0]

        GroupService.add_documents(first, [document])
        GroupService.add_documents(second, [document])

        assert {group.name for group in document.groups} == {"Assunto A", "Assunto B"}

    def test_removing_from_a_group_keeps_the_document(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)

        removed = GroupService.remove_documents(group, [three_documents[1]])

        assert removed == 1
        assert len(GroupRepository.documents_of(group)) == 2
        assert DocumentRepository.get_by_uuid(three_documents[1].uuid) is not None

    def test_a_trashed_document_leaves_the_listing_but_keeps_its_membership(
        self, app, group, three_documents
    ):
        """The trash is reversible, so the grouping waits for the decision."""
        from app.services.document_service import DocumentService

        GroupService.add_documents(group, three_documents)
        DocumentService.move_to_trash(three_documents[0])

        assert len(GroupRepository.documents_of(group)) == 2
        assert GroupRepository.contains(group.id, three_documents[0].id)

    def test_purging_a_document_drops_its_memberships(
        self, app, db, group, three_documents
    ):
        from app.services.document_service import DocumentService

        GroupService.add_documents(group, three_documents)
        document = three_documents[0]
        DocumentService.move_to_trash(document)
        DocumentService.purge(document)

        remaining = db.session.execute(db.select(document_groups)).all()
        assert len(remaining) == 2

    def test_emptying_the_trash_leaves_no_orphan_memberships(
        self, app, db, group, three_documents
    ):
        """The bulk DELETE bypasses the ORM, so this leans on ON DELETE CASCADE."""
        from app.services.document_service import DocumentService

        GroupService.add_documents(group, three_documents)
        for document in three_documents:
            DocumentService.move_to_trash(document)

        DocumentService.empty_trash()

        assert db.session.execute(db.select(document_groups)).all() == []
        assert GroupRepository.get_by_name("Manual do produto") is not None

    def test_setting_the_groups_of_a_document_replaces_them(self, app, three_documents):
        first = GroupService.create("Antes")
        second = GroupService.create("Depois")
        document = three_documents[0]
        GroupService.add_documents(first, [document])

        GroupService.set_groups_for(document, [second.uuid])

        assert [group.name for group in document.groups] == ["Depois"]

    def test_unknown_group_uuids_are_ignored(self, app, three_documents):
        document = three_documents[0]
        GroupService.set_groups_for(document, ["nao-existe"])
        assert document.groups == []


class TestBoundedWork:
    """Ceilings that keep a page render and a write from being unbounded."""

    def test_a_group_cannot_grow_past_its_ceiling(self, app, make_document, monkeypatch):
        from app.services import group_service

        monkeypatch.setattr(group_service, "MAX_DOCUMENTS_PER_GROUP", 3)
        created = GroupService.create("Cheio")
        documents = [make_document(title=f"Doc {index}") for index in range(4)]

        GroupService.add_documents(created, documents[:3])

        with pytest.raises(ValidationError) as excinfo:
            GroupService.add_documents(created, documents[3:])

        assert "até 3 documentos" in excinfo.value.message
        assert len(GroupRepository.documents_of(created)) == 3

    def test_adding_a_batch_costs_a_fixed_number_of_queries(
        self, app, db, group, make_document
    ):
        """Found in review: one "is it already here?" query per document."""
        from tests.test_performance import QueryCounter

        few = [make_document(title=f"Poucos {index}") for index in range(2)]
        many = [make_document(title=f"Muitos {index}") for index in range(20)]

        with QueryCounter() as small:
            GroupService.add_documents(group, few)
        with QueryCounter() as large:
            GroupService.add_documents(group, many)

        assert large.count == small.count, (
            f"N+1 ao adicionar: {small.count} consultas para 2 documentos, "
            f"{large.count} para 20"
        )

    def test_reordering_costs_a_fixed_number_of_queries(
        self, app, group, make_document
    ):
        from tests.test_performance import QueryCounter

        documents = [make_document(title=f"Ordem {index}") for index in range(20)]
        GroupService.add_documents(group, documents)
        uuids = [document.uuid for document in documents]

        with QueryCounter() as small:
            GroupService.reorder(group, uuids[:2] + uuids[2:])
        with QueryCounter() as large:
            GroupService.reorder(group, list(reversed(uuids)))

        assert large.count == small.count, (
            f"reordenar emitiu {large.count} consultas para 20 documentos"
        )

    def test_setting_groups_from_the_editor_is_one_transaction(
        self, app, db, three_documents
    ):
        """A half-applied panel save would leave the document in both states."""
        first = GroupService.create("Entra")
        second = GroupService.create("Sai")
        document = three_documents[0]
        GroupService.add_documents(second, [document])

        committed: list[int] = []
        from sqlalchemy import event

        def count_commit(_session):
            committed.append(1)

        event.listen(db.session, "after_commit", count_commit)
        try:
            GroupService.set_groups_for(document, [first.uuid])
        finally:
            event.remove(db.session, "after_commit", count_commit)

        assert len(committed) == 1, f"{len(committed)} commits para uma troca de grupos"
        assert [group.name for group in document.groups] == ["Entra"]


class TestOrder:
    def test_reordering_rewrites_the_whole_sequence(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)
        reversed_uuids = [document.uuid for document in reversed(three_documents)]

        GroupService.reorder(group, reversed_uuids)

        assert [d.uuid for d in GroupRepository.documents_of(group)] == reversed_uuids

    def test_documents_missing_from_the_request_keep_their_place_at_the_end(
        self, app, group, three_documents
    ):
        """A stale page must never silently drop a document from the group."""
        GroupService.add_documents(group, three_documents)

        GroupService.reorder(group, [three_documents[2].uuid])

        result = [d.display_title for d in GroupRepository.documents_of(group)]
        assert result[0] == "Capítulo 3"
        assert len(result) == 3

    def test_moving_up_and_down(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)

        GroupService.move(group, three_documents[2], -1)
        assert [d.display_title for d in GroupRepository.documents_of(group)] == [
            "Capítulo 1", "Capítulo 3", "Capítulo 2",
        ]

        GroupService.move(group, three_documents[2], 1)
        assert [d.display_title for d in GroupRepository.documents_of(group)] == [
            "Capítulo 1", "Capítulo 2", "Capítulo 3",
        ]

    def test_moving_past_the_edge_does_nothing(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)

        assert GroupService.move(group, three_documents[0], -1) is False
        assert [d.display_title for d in GroupRepository.documents_of(group)][0] == (
            "Capítulo 1"
        )

    def test_moving_a_document_that_is_not_in_the_group(self, app, group, three_documents):
        with pytest.raises(NotFoundError):
            GroupService.move(group, three_documents[0], 1)

    def test_the_same_document_can_hold_different_places_in_two_groups(
        self, app, three_documents
    ):
        first = GroupService.create("Um")
        second = GroupService.create("Dois")
        GroupService.add_documents(first, three_documents)
        GroupService.add_documents(second, list(reversed(three_documents)))

        assert GroupRepository.documents_of(first)[0].uuid == three_documents[0].uuid
        assert GroupRepository.documents_of(second)[0].uuid == three_documents[2].uuid


class TestScreens:
    def test_the_index_lists_groups_with_their_counts(self, client, group, three_documents):
        GroupService.add_documents(group, three_documents)

        response = client.get("/grupos/")

        assert response.status_code == 200
        assert "Manual do produto".encode() in response.data
        assert b"3</span> documento" in response.data.replace(b"\n", b"")

    def test_a_group_can_be_created_from_the_page(self, client, app):
        response = client.post(
            "/grupos/", data={"name": "Curso de Markdown", "color": "#4F46E5"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert GroupRepository.get_by_name("Curso de Markdown") is not None

    def test_the_detail_page_shows_documents_and_candidates(
        self, client, group, three_documents
    ):
        GroupService.add_documents(group, [three_documents[0]])

        response = client.get(f"/grupos/{group.uuid}")
        body = response.data.decode()

        assert response.status_code == 200
        assert "Capítulo 1" in body
        # The other two are offered for adding, not listed as members.
        assert "Capítulo 2" in body

    def test_an_unknown_group_is_a_404(self, client, app):
        response = client.get("/grupos/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_the_detail_page_keeps_the_accessibility_baseline(
        self, client, group, three_documents
    ):
        """The same structural rules the other screens are held to."""
        import re

        GroupService.add_documents(group, three_documents)
        html = client.get(f"/grupos/{group.uuid}").data.decode()

        assert len(re.findall(r"<h1\b", html)) == 1
        assert "<main" in html and "<nav" in html

        for match in re.finditer(r"<button\b[^>]*>(.*?)</button>", html, re.S):
            attrs, inner = match.group(0), match.group(1)
            has_text = re.sub(r"<[^>]+>", "", inner).strip()
            assert has_text or "aria-label=" in attrs, f"botão sem nome: {attrs[:120]}"

    def test_documents_can_be_added_from_the_page(self, client, group, three_documents):
        response = client.post(
            f"/grupos/{group.uuid}/adicionar",
            data={"uuids": [document.uuid for document in three_documents]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert len(GroupRepository.documents_of(group)) == 3

    def test_a_document_can_be_removed_from_the_page(self, client, group, three_documents):
        GroupService.add_documents(group, three_documents)

        client.post(
            f"/grupos/{group.uuid}/remover/{three_documents[0].uuid}",
            follow_redirects=True,
        )

        assert len(GroupRepository.documents_of(group)) == 2

    def test_the_arrows_reorder_without_javascript(self, client, group, three_documents):
        GroupService.add_documents(group, three_documents)

        client.post(
            f"/grupos/{group.uuid}/mover/{three_documents[2].uuid}",
            data={"direcao": "cima"},
            follow_redirects=True,
        )

        assert [d.display_title for d in GroupRepository.documents_of(group)] == [
            "Capítulo 1", "Capítulo 3", "Capítulo 2",
        ]

    def test_a_group_can_be_edited_from_the_dialog(self, client, group):
        response = client.post(
            f"/grupos/{group.uuid}/editar",
            data={
                "name": "Manual do produto v3",
                "description": "Agora com a linha nova.",
                "color": "#DC2626",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert group.name == "Manual do produto v3"
        assert group.color == "#DC2626"

    def test_renaming_onto_an_existing_name_is_refused_with_a_message(
        self, client, group
    ):
        GroupService.create("Já existe")

        response = client.post(
            f"/grupos/{group.uuid}/editar",
            data={"name": "Já existe", "color": "#4F46E5"},
            follow_redirects=True,
        )

        assert "Já existe um grupo".encode() in response.data
        assert group.name == "Manual do produto"

    def test_creating_a_duplicate_from_the_page_shows_the_reason(self, client, group):
        response = client.post(
            "/grupos/",
            data={"name": "Manual do produto", "color": "#4F46E5"},
            follow_redirects=True,
        )

        assert "Já existe um grupo".encode() in response.data

    def test_an_empty_name_is_refused_with_a_message(self, client, app):
        response = client.post(
            "/grupos/", data={"name": "   ", "color": "#4F46E5"}, follow_redirects=True
        )

        assert "Informe o nome do grupo".encode() in response.data

    def test_moving_a_document_that_is_not_in_the_group_reports_it(
        self, client, group, three_documents
    ):
        GroupService.add_documents(group, [three_documents[0]])

        response = client.post(
            f"/grupos/{group.uuid}/mover/{three_documents[1].uuid}",
            data={"direcao": "cima"},
            follow_redirects=True,
        )

        assert "não está neste grupo".encode() in response.data

    def test_removing_a_document_that_was_never_there(self, client, group, three_documents):
        response = client.post(
            f"/grupos/{group.uuid}/remover/{three_documents[0].uuid}",
            follow_redirects=True,
        )

        assert "não estava no grupo".encode() in response.data

    def test_adding_nothing_reports_it(self, client, group):
        response = client.post(
            f"/grupos/{group.uuid}/adicionar", data={}, follow_redirects=True
        )

        assert "Nenhum documento novo".encode() in response.data

    def test_deleting_a_group_from_the_page(self, client, db, group):
        response = client.post(f"/grupos/{group.uuid}/excluir", follow_redirects=True)

        assert response.status_code == 200
        assert db.session.scalars(db.select(Group)).all() == []

    def test_group_actions_require_csrf(self, csrf_app):
        with csrf_app.app_context():
            created = GroupService.create("Protegido")
            client = csrf_app.test_client()
            response = client.post(f"/grupos/{created.uuid}/excluir")

        assert response.status_code == 400


class TestReorderEndpoint:
    def test_a_drag_persists_the_new_order(self, client, group, three_documents):
        GroupService.add_documents(group, three_documents)
        reversed_uuids = [document.uuid for document in reversed(three_documents)]

        response = client.post(
            f"/api/grupos/{group.uuid}/ordem", json={"uuids": reversed_uuids}
        )

        assert response.status_code == 200
        assert response.get_json()["total"] == 3
        assert [d.uuid for d in GroupRepository.documents_of(group)] == reversed_uuids

    def test_a_malformed_payload_is_refused(self, client, group):
        response = client.post(f"/api/grupos/{group.uuid}/ordem", json={"uuids": "abc"})
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "payload",
        [{"uuids": [1, 2]}, {"uuids": {"a": 1}}, {}, {"uuids": None}],
    )
    def test_hostile_payloads_are_refused(self, client, group, payload):
        response = client.post(f"/api/grupos/{group.uuid}/ordem", json=payload)
        assert response.status_code == 400

    def test_an_oversized_payload_cannot_ask_for_unbounded_work(
        self, client, group, three_documents
    ):
        """The cap is what keeps a crafted request from becoming N updates."""
        from app.services.group_service import MAX_DOCUMENTS_PER_OPERATION

        GroupService.add_documents(group, three_documents)
        flood = [f"uuid-{index}" for index in range(MAX_DOCUMENTS_PER_OPERATION * 5)]

        response = client.post(f"/api/grupos/{group.uuid}/ordem", json={"uuids": flood})

        assert response.status_code == 200
        assert response.get_json()["total"] == 3

    def test_reordering_cannot_pull_in_a_document_from_another_group(
        self, client, app, group, three_documents
    ):
        other = GroupService.create("Outro grupo")
        GroupService.add_documents(group, three_documents[:1])
        GroupService.add_documents(other, three_documents[1:])

        client.post(
            f"/api/grupos/{group.uuid}/ordem",
            json={"uuids": [document.uuid for document in three_documents]},
        )

        assert len(GroupRepository.documents_of(group)) == 1
        assert len(GroupRepository.documents_of(other)) == 2

    def test_the_reorder_endpoint_requires_csrf(self, csrf_app):
        with csrf_app.app_context():
            created = GroupService.create("Protegido")
            client = csrf_app.test_client()
            response = client.post(
                f"/api/grupos/{created.uuid}/ordem", json={"uuids": []}
            )

        assert response.status_code == 400

    def test_an_unknown_group_is_a_404(self, client, app):
        response = client.post(
            "/api/grupos/00000000-0000-0000-0000-000000000000/ordem",
            json={"uuids": []},
        )
        assert response.status_code == 404


class TestListingIntegration:
    def test_documents_can_be_filtered_by_group(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents[:2])

        page = DocumentRepository.paginate(DocumentQuery(group_uuid=group.uuid))

        assert page.total == 2

    def test_each_document_appears_once_when_filtering(self, app, group, three_documents):
        GroupService.add_documents(group, three_documents)

        page = DocumentRepository.paginate(DocumentQuery(group_uuid=group.uuid))
        ids = [document.id for document in page.items]

        assert len(ids) == len(set(ids))

    def test_an_unknown_group_filter_matches_nothing(self, app, three_documents):
        page = DocumentRepository.paginate(DocumentQuery(group_uuid="nao-existe"))
        assert page.total == 0

    def test_the_listing_offers_the_group_filter(self, client, group):
        response = client.get("/documentos/")
        assert b"Todos os grupos" in response.data

    def test_a_bulk_selection_can_be_added_to_a_group(
        self, client, group, three_documents
    ):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={
                "acao": "group",
                "grupo": group.uuid,
                "uuids": [document.uuid for document in three_documents],
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert len(GroupRepository.documents_of(group)) == 3

    def test_a_bulk_add_without_a_group_is_refused(self, client, three_documents):
        response = client.post(
            "/documentos/acoes-em-massa",
            data={"acao": "group", "uuids": [three_documents[0].uuid]},
            follow_redirects=True,
        )

        assert "Escolha um grupo".encode() in response.data


class TestEditorPanel:
    def test_the_panel_lists_every_group_and_ticks_the_current_ones(
        self, client, group, three_documents
    ):
        document = three_documents[0]
        GroupService.add_documents(group, [document])
        GroupService.create("Outro assunto")

        response = client.get(f"/editor/{document.uuid}")
        body = response.data.decode()

        assert "Manual do produto" in body
        assert "Outro assunto" in body
        assert f'value="{group.uuid}"' in body

    def test_saving_the_panel_updates_the_groups(self, client, group, three_documents):
        document = three_documents[0]
        other = GroupService.create("Outro assunto")

        client.post(
            f"/editor/{document.uuid}",
            data={
                "title": document.title,
                "content_markdown": document.content_markdown,
                "expected_revision": str(document.revision),
                "groups": [group.uuid, other.uuid],
                "page_size": "A4",
                "pdf_theme": "classic",
            },
            follow_redirects=True,
        )

        assert {g.name for g in document.groups} == {"Manual do produto", "Outro assunto"}


class TestBackup:
    def test_groups_survive_an_export_and_restore(self, app, db, group, three_documents):
        from app.services.backup_service import (
            build_export_payload,
            create_backup,
            restore_backup,
        )

        GroupService.add_documents(group, three_documents)
        payload = build_export_payload()
        assert payload["groups"][0]["name"] == "Manual do produto"
        assert payload["documents"][0]["groups"] == ["Manual do produto"]

        info = create_backup()
        restore_backup(info.path, mode="replace")

        restored = GroupRepository.get_by_name("Manual do produto")
        assert restored is not None
        assert restored.description == "Tudo sobre a versão 2."
        assert len(GroupRepository.documents_of(restored)) == 3

    def test_an_empty_group_is_not_lost(self, app, group):
        from app.services.backup_service import create_backup, restore_backup

        info = create_backup()
        restore_backup(info.path, mode="replace")

        assert GroupRepository.get_by_name("Manual do produto") is not None

    def test_an_archive_without_groups_still_restores(self, app, make_document):
        """Archives written before groups existed carry no such key."""
        from app.services.backup_service import _restore_document

        document = _restore_document(
            {"title": "Antigo", "content_markdown": "corpo", "uuid": "u-1"}
        )

        assert document.groups == []
