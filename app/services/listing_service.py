"""Document listing: request parsing, search resolution and pagination.

This is the seam that keeps the dependency direction honest. Full-text search
lives in a service, pagination lives in a repository, and something has to
join them — doing it inside the repository would mean the data layer importing
the service layer.

It also owns the snippet strategy, so callers do not have to know whether the
highlights came from FTS5 or from the LIKE fallback.

And it owns :func:`build_query`: the single reading of what a set of filter
parameters *means*. The listing screen parses the URL with it; a bulk action
applied to "every result" parses the same parameters, carried back in the
form, with the same function. Two readings of one query string is how a
selection ends up acting on a set the user was never shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup
from werkzeug.datastructures import MultiDict

from app.repositories.document_repository import (
    SCOPE_ACTIVE,
    SCOPE_ALL,
    SCOPE_ARCHIVED,
    SEARCH_SORT_OPTIONS,
    SORT_OPTIONS,
    SORT_RELEVANCE,
    WITHOUT,
    DocumentQuery,
    DocumentRepository,
)
from app.services.search_service import highlight_terms, search_index
from app.utils.params import positive_int

#: Scopes a URL may name. The trash is deliberately absent: it has a screen of
#: its own, and a listing filter that could reach deleted documents would let
#: a bulk action reach them too.
VALID_SCOPES = {SCOPE_ACTIVE, SCOPE_ARCHIVED, SCOPE_ALL}

#: Ceilings on what a single request may carry, so a hand-made query string
#: cannot turn parsing into the expensive part.
MAX_SEARCH_LENGTH = 200
MAX_TAG_FILTERS = 5


def build_query(args: MultiDict, per_page: int = 12) -> DocumentQuery:
    """Read a set of filter parameters into a :class:`DocumentQuery`.

    Takes any mapping that answers ``get``/``getlist`` - ``request.args`` for
    the page being rendered, or the filters a bulk form carried back - so the
    two can never disagree about what "sem categoria, ordenado por título"
    selects.
    """
    scope = args.get("escopo", SCOPE_ACTIVE)
    search = (args.get("q") or "").strip()[:MAX_SEARCH_LENGTH]

    # The toolbar sends the tags already chosen plus the one just picked, so
    # the same slug can arrive twice - deduplicated before the ceiling, or a
    # repeat would spend one of the five slots on a filter already applied.
    #
    # "Sem etiqueta" is a statement about the whole set, so it cannot share the
    # filter with a tag: asking for the documents that carry no tag *and* carry
    # this one is a question with no answer.
    tag_slugs = tuple(dict.fromkeys(tag for tag in args.getlist("etiqueta") if tag))
    tag_slugs = tag_slugs[:MAX_TAG_FILTERS]
    if WITHOUT in tag_slugs:
        tag_slugs = (WITHOUT,)

    # One control, so one parameter: "sem categoria" is a value of `categoria`,
    # not a flag beside it. Anything that is neither the sentinel nor digits -
    # a hand-edited URL, a stale bookmark - falls through to "all categories".
    categoria = (args.get("categoria") or "").strip()

    # A search with no ordering of its own is ranked, not dated: the answer to
    # "where is that document" is the best match, never the most recent one.
    sort = args.get("ordem") or ""
    if sort not in (SEARCH_SORT_OPTIONS if search else SORT_OPTIONS):
        sort = SORT_RELEVANCE if search else "updated_desc"

    return DocumentQuery(
        search=search,
        category_id=positive_int(categoria),
        without_category=categoria == WITHOUT,
        group_uuid=(args.get("grupo") or "").strip()[:36],
        tag_slugs=tag_slugs,
        only_favorites=args.get("favoritos") == "1",
        scope=scope if scope in VALID_SCOPES else SCOPE_ACTIVE,
        sort=sort,
        page=positive_int(args.get("pagina")) or 1,
        per_page=per_page,
    )


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
