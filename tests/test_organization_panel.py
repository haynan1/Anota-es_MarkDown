"""Saving a tag from inside the editor.

The organisation panel shares one form with the whole editor: it carries the
document's text along so it still saves without JavaScript. That made it the
one place where saving an *organisation* detail could be refused for a reason
belonging to the *text* - and it was, on every save that followed an autosave.
"""

from __future__ import annotations

import re

import pytest

from app.services.document_service import DocumentService
from app.services.exceptions import ConflictError
from app.services.group_service import GroupService


def _meta_post(client, document, **overrides):
    data = {
        "title": document.title,
        "content_markdown": document.content_markdown,
        "expected_revision": str(document.revision),
        "category_id": "",
        "tags": "",
        "page_size": "A4",
        "pdf_theme": "classic",
    }
    data.update(overrides)
    return client.post(f"/editor/{document.uuid}", data=data, follow_redirects=True)


class TestTheFormItself:
    def test_the_revision_is_carried_by_exactly_one_input(self, client, document):
        """Two inputs of one name is a silent bug: the browser sends both.

        ``hidden_tag()`` renders every hidden field, so writing the mirror by
        hand put a second ``expected_revision`` in the form. The server read
        the first, the editor updated the second, and the two drifted apart
        the moment anything autosaved.
        """
        html = client.get(f"/editor/{document.uuid}").get_data(as_text=True)
        assert len(re.findall(r'name="expected_revision"', html)) == 1

    def test_the_single_input_is_the_one_the_editor_updates(self, client, document):
        html = client.get(f"/editor/{document.uuid}").get_data(as_text=True)
        field = re.search(r"<input[^>]*name=\"expected_revision\"[^>]*>", html).group(0)
        assert "data-mirror-revision" in field

    def test_csrf_protection_survives_the_change(self, csrf_app):
        """`csrf_token` replaced `hidden_tag()`; the token must still be there."""
        with csrf_app.app_context():
            document = DocumentService.create(title="Protegido", content_markdown="x")
            client = csrf_app.test_client()
            html = client.get(f"/editor/{document.uuid}").get_data(as_text=True)
            assert 'name="csrf_token"' in html

            response = client.post(
                f"/editor/{document.uuid}",
                data={"title": "Sem token", "content_markdown": "x", "tags": "nova"},
            )
            assert response.status_code in {200, 400}
            assert document.tag_names == []


class TestSavingOrganisationAfterAnAutosave:
    def test_a_tag_saves_even_when_the_revision_is_stale(self, client, document):
        """The exact shape of the bug: autosave moves on, the panel does not.

        Nothing in this submission changes the text, so there is nothing to
        overwrite and nothing to conflict about.
        """
        stale = document.revision
        client.post(
            f"/api/documentos/{document.uuid}/autosave",
            json={
                "title": document.title,
                "content_markdown": "Uma linha nova, digitada antes de abrir o painel.",
                "revision": stale,
            },
        )
        assert document.revision > stale

        response = _meta_post(
            client,
            document,
            content_markdown=document.content_markdown,
            expected_revision=str(stale),
            tags="projeto",
        )
        html = response.get_data(as_text=True)
        assert "alterado em outro lugar" not in html
        assert document.tag_names == ["projeto"]

    def test_groups_save_the_same_way(self, client, document):
        group = GroupService.create("Pesquisa")
        stale = document.revision
        DocumentService.save(document, document.title, "Texto novo, salvo em outro lugar.")

        _meta_post(client, document, expected_revision=str(stale), groups=[group.uuid])
        assert [item.name for item in document.groups] == ["Pesquisa"]


class TestTheGuardStillGuards:
    def test_a_real_overwrite_is_still_refused(self, app, document):
        stale = document.revision
        DocumentService.save(document, document.title, "Escrito em outra aba.")

        with pytest.raises(ConflictError):
            DocumentService.save(
                document,
                document.title,
                "Texto diferente, baseado na versão antiga.",
                expected_revision=stale,
            )

    def test_a_refused_save_changes_nothing_at_all(self, client, document):
        """A rejected submission must not leave the taxonomy behind it.

        The panel wrote tags and groups before asking whether the save was
        allowed, so a conflict reported failure and changed the document
        anyway.
        """
        group = GroupService.create("Arquivo")
        stale = document.revision
        DocumentService.save(document, document.title, "Escrito em outra aba.")

        response = _meta_post(
            client,
            document,
            content_markdown="Conteúdo conflitante vindo do painel.",
            expected_revision=str(stale),
            tags="nao-deveria-existir",
            groups=[group.uuid],
        )
        assert "alterado em outro lugar" in response.get_data(as_text=True)
        assert document.tag_names == []
        assert list(document.groups) == []
        assert document.content_markdown == "Escrito em outra aba."

    def test_an_unchanged_save_never_conflicts(self, app, document):
        result = DocumentService.save(
            document,
            document.title,
            document.content_markdown,
            expected_revision=document.revision - 99,
        )
        assert result.content_changed is False
        assert result.version_created is False
