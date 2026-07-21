"""Document listing: search resolution plus pagination.

This is the seam that keeps the dependency direction honest. Full-text search
lives in a service, pagination lives in a repository, and something has to
join them — doing it inside the repository would mean the data layer importing
the service layer.

It also owns the snippet strategy, so callers do not have to know whether the
highlights came from FTS5 or from the LIKE fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup

from app.repositories.document_repository import DocumentQuery, DocumentRepository
from app.services.search_service import highlight_terms, search_index


@dataclass(slots=True)
class ListingResult:
    pagination: object
    snippets: dict[int, Markup]
    used_full_text: bool

    @property
    def items(self) -> list:
        return self.pagination.items

    @property
    def total(self) -> int:
        return self.pagination.total


def resolve_search(query: DocumentQuery) -> bool:
    """Populate ``query.matched_ids``. Returns True when FTS answered."""
    if not query.is_searching:
        query.matched_ids = None
        return False

    matched = search_index.search_ids(query.search.strip())
    query.matched_ids = matched
    return matched is not None


def list_documents(query: DocumentQuery, with_snippets: bool = True) -> ListingResult:
    """Run a listing query and build the highlighted excerpts for it."""
    used_full_text = resolve_search(query)
    pagination = DocumentRepository.paginate(query)

    snippets: dict[int, Markup] = {}
    if with_snippets and query.is_searching and pagination.items:
        document_ids = [document.id for document in pagination.items]

        if used_full_text:
            snippets = search_index.snippets(query.search, document_ids)

        # Either FTS is unavailable, or it matched a document whose snippet it
        # could not build. Fall back so a search result is never left without
        # its highlight.
        for document in pagination.items:
            if document.id not in snippets:
                snippets[document.id] = highlight_terms(
                    document.excerpt or document.title, query.search
                )

    return ListingResult(
        pagination=pagination, snippets=snippets, used_full_text=used_full_text
    )
