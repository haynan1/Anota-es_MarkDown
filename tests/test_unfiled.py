"""Documents that belong to no group and documents that carry no tag.

A library grows its blind spot one unfiled document at a time. Every filter on
the listing used to be a question about membership - "what is inside this
group", "what wears this label" - and none of them could ask the one question
that finds what is falling through: what is inside *nothing*.
"""

from __future__ import annotations

from app.repositories.document_repository import (
    WITHOUT,
    DocumentQuery,
    DocumentRepository,
)
from app.services.group_service import GroupService
from app.services.listing_service import list_documents


def _listing(**kwargs):
    return list_documents(DocumentQuery(per_page=50, **kwargs)).pagination


class TestWithoutGroup:
    def test_finds_only_the_documents_no_group_claimed(self, app, make_document):
        grouped = make_document(title="Já organizado")
        loose = make_document(title="Solto por aí")
        GroupService.add_documents(GroupService.create("Projeto"), [grouped])

        results = _listing(group_uuid=WITHOUT)
        assert [item.title for item in results.items] == ["Solto por aí"]
        assert loose.uuid in {item.uuid for item in results.items}

    def test_a_document_removed_from_its_last_group_comes_back(self, app, make_document):
        document = make_document(title="Entra e sai")
        group = GroupService.create("Temporário")
        GroupService.add_documents(group, [document])
        assert _listing(group_uuid=WITHOUT).total == 0

        GroupService.remove_documents(group, [document])
        assert _listing(group_uuid=WITHOUT).total == 1

    def test_membership_in_any_group_is_enough(self, app, make_document):
        """Being in one group of three still counts as filed."""
        document = make_document(title="Em um grupo só")
        GroupService.add_documents(GroupService.create("Um"), [document])
        GroupService.create("Dois")
        GroupService.create("Três")

        assert _listing(group_uuid=WITHOUT).total == 0

    def test_combines_with_the_other_filters(self, app, make_document):
        make_document(title="Solto e favorito", is_favorite=True)
        make_document(title="Solto e comum")

        results = _listing(group_uuid=WITHOUT, only_favorites=True)
        assert [item.title for item in results.items] == ["Solto e favorito"]


class TestWithoutTags:
    def test_finds_only_the_documents_nobody_labelled(self, app, make_document):
        make_document(title="Etiquetado", tag_names=["projeto"])
        make_document(title="Sem etiqueta nenhuma")

        results = _listing(tag_slugs=(WITHOUT,))
        assert [item.title for item in results.items] == ["Sem etiqueta nenhuma"]

    def test_stripping_the_last_tag_brings_the_document_back(self, app, make_document, db):
        from app.services.document_service import DocumentService

        document = make_document(title="Perde a etiqueta", tag_names=["temp"])
        assert _listing(tag_slugs=(WITHOUT,)).total == 0

        DocumentService.apply_metadata(document, tag_names=[])
        db.session.commit()
        assert _listing(tag_slugs=(WITHOUT,)).total == 1

    def test_the_sentinel_never_collides_with_a_real_tag(self, app, make_document):
        """A tag literally named like the sentinel is still just a tag.

        Slugs come out of ``safe_slug`` as ``[a-z0-9-]``, so no tag can ever
        own the reserved value - which is the whole reason it holds an "@".
        """
        document = make_document(title="Etiqueta esperta", tag_names=[WITHOUT])
        assert WITHOUT not in {tag.slug for tag in document.tags}
        assert _listing(tag_slugs=(WITHOUT,)).total == 0


class TestCounts:
    def test_orphan_counts_answers_both_questions_at_once(self, app, make_document):
        filed = make_document(title="Arrumado", tag_names=["ok"])
        GroupService.add_documents(GroupService.create("Casa"), [filed])
        make_document(title="Perdido")

        ungrouped, untagged = DocumentRepository.orphan_counts()
        assert (ungrouped, untagged) == (1, 1)

    def test_the_trash_is_not_counted(self, app, make_document):
        from app.services.document_service import DocumentService

        document = make_document(title="Vai para a lixeira")
        assert DocumentRepository.orphan_counts() == (1, 1)

        DocumentService.move_to_trash(document)
        assert DocumentRepository.orphan_counts() == (0, 0)


class TestListingScreen:
    def test_the_toolbar_offers_both_filters_with_their_counts(self, client, make_document):
        make_document(title="Solto")

        html = client.get("/documentos/").get_data(as_text=True)
        assert 'id="filter-tag"' in html
        assert "Sem grupo (1)" in html
        assert "Sem etiqueta (1)" in html

    def test_the_filters_name_the_view_they_produced(self, client, make_document):
        make_document(title="Solto")

        assert "Fora de qualquer grupo" in client.get(
            "/documentos/", query_string={"grupo": WITHOUT}
        ).get_data(as_text=True)
        assert "Sem etiqueta" in client.get(
            "/documentos/", query_string={"etiqueta": WITHOUT}
        ).get_data(as_text=True)

    def test_without_tags_never_travels_with_a_tag(self, client, make_document):
        """The two together are a question with no answer, so one of them wins.

        Without this, choosing "Sem etiqueta" while a tag chip was active
        would produce an always-empty listing and no way to see why.
        """
        make_document(title="Etiquetado", tag_names=["projeto"])
        make_document(title="Solto")

        html = client.get(
            "/documentos/", query_string=[("etiqueta", "projeto"), ("etiqueta", WITHOUT)]
        ).get_data(as_text=True)
        assert "Solto" in html
        assert "1 documento" in html

    def test_pagination_keeps_the_tag_filter(self, client, make_document):
        """The pager used to drop `etiqueta`, widening the listing on page 2."""
        for index in range(15):
            make_document(title=f"Etiquetado {index}", tag_names=["lote"])
        make_document(title="Fora do lote")

        html = client.get(
            "/documentos/", query_string={"etiqueta": "lote"}
        ).get_data(as_text=True)
        assert "etiqueta=lote" in html


class TestTagFilterAccumulation:
    def test_the_same_tag_twice_spends_one_slot(self, client, make_document):
        """The toolbar resends the active tags plus the new one.

        Without the dedup a repeat would eat one of the five slots and push a
        real filter off the end of the list.
        """
        make_document(title="Com as duas", tag_names=["alpha", "beta"])
        make_document(title="Só alpha", tag_names=["alpha"])

        html = client.get(
            "/documentos/",
            query_string=[("etiqueta", "alpha"), ("etiqueta", "alpha"), ("etiqueta", "beta")],
        ).get_data(as_text=True)
        assert "Com as duas" in html
        assert "Só alpha" not in html

    def test_tags_combine_with_and(self, client, make_document):
        make_document(title="Ambas", tag_names=["alpha", "beta"])
        make_document(title="Apenas alpha", tag_names=["alpha"])

        html = client.get(
            "/documentos/", query_string=[("etiqueta", "alpha"), ("etiqueta", "beta")]
        ).get_data(as_text=True)
        assert "Ambas" in html
        assert "Apenas alpha" not in html

    def test_the_select_never_offers_a_tag_already_filtered(self, client, make_document):
        make_document(title="Etiquetado", tag_names=["alpha"])

        html = client.get(
            "/documentos/", query_string={"etiqueta": "alpha"}
        ).get_data(as_text=True)
        assert '<option value="alpha"' not in html
