"""What "the selected documents" means, for every bulk action that asks.

The listing offers two ways to choose documents, and they are answered very
differently:

* **Ticked boxes.** The client gathers the UUIDs it has on screen and posts
  them. Bounded by what one request may carry.
* **Every result.** The client posts no identifiers at all - only the filters
  the page was already showing. The server re-runs that query and resolves the
  set itself, so a selection of four hundred documents costs the same request
  as a selection of four, and nothing about it can be forged into a set the
  user was never shown: the filters are re-read through the same
  :func:`~app.services.listing_service.build_query` that rendered the page,
  with the same ceilings and the same scope. The trash is unreachable from
  there, so it stays unreachable from here.

Both collapse into the same thing before any action sees them: a list of
primary keys. Every bulk action - archive, lock, category, group, export -
therefore consumes one shape, and none of them can grow its own idea of what
was selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl

from werkzeug.datastructures import MultiDict

from app.repositories.document_repository import DocumentRepository
from app.services.document_service import MAX_BULK_SELECTION
from app.services.listing_service import build_query, resolve_search

#: Form field naming which of the two modes the client is asking for.
SELECTION_FIELD = "selecao"
#: Its value for "every result", rather than the boxes that happen to be ticked.
MODE_FILTER = "filtro"
#: Form field carrying the listing's own query string back to the server.
FILTERS_FIELD = "filtros"

#: Ceiling on a filter-built selection. Higher than the ticked-box ceiling and
#: for a different reason: the request carries no identifiers, so nothing here
#: is bounded by request size - only by how many rows one action should touch
#: without the user having seen them pass. Comfortably inside SQLite's bound
#: parameter budget, which is what the resulting ``IN`` clause spends.
MAX_FILTER_SELECTION = 2000

#: A filter string longer than this is not a listing anyone navigated to.
MAX_FILTERS_LENGTH = 2000


@dataclass(slots=True)
class Selection:
    """The documents a bulk action is about to touch."""

    ids: list[int]
    #: True when the whole filtered set was asked for, rather than ticked boxes.
    from_filters: bool
    #: The ceiling that was applied. Carried rather than looked up again by the
    #: caller: the two modes are bounded differently, and a message quoting a
    #: number the resolution did not use would be worse than no number.
    limit: int
    #: The ceiling cut the set short. The caller must say so: a bulk action
    #: that silently stops at 2000 of 3000 documents is the one failure here
    #: that a user could not detect on their own.
    truncated: bool

    def __len__(self) -> int:
        return len(self.ids)

    def __bool__(self) -> bool:
        return bool(self.ids)


def resolve(
    form: MultiDict, include_deleted: bool = False, limit: int = MAX_BULK_SELECTION
) -> Selection:
    """Resolve a submitted bulk form to the documents it selected.

    ``include_deleted`` applies only to the ticked-box mode, and only because
    lock and unlock reach documents in any state. The filtered mode can never
    need it: the listing it re-runs cannot show the trash in the first place.
    """
    if form.get(SELECTION_FIELD) == MODE_FILTER:
        return _from_filters(form.get(FILTERS_FIELD) or "")

    uuids = [uuid for uuid in form.getlist("uuids") if uuid][:limit]
    return Selection(
        ids=DocumentRepository.ids_for_uuids(uuids, include_deleted=include_deleted),
        from_filters=False,
        limit=limit,
        truncated=False,
    )


def _from_filters(filters: str) -> Selection:
    """Every document the given listing query matches, up to the ceiling."""
    args = MultiDict(parse_qsl(filters[:MAX_FILTERS_LENGTH], keep_blank_values=True))
    query = build_query(args)

    # The same resolution the listing does before paginating: without it a
    # search would fall through to the LIKE branch here while the page the
    # user was looking at had been answered by the full-text index, and the
    # two sets are not always the same.
    resolve_search(query)

    # One more than the ceiling, so "there was more than this" is answered by
    # the same query rather than by a second COUNT over the whole set.
    limit = MAX_FILTER_SELECTION
    ids = DocumentRepository.ids_matching(query, limit=limit + 1)
    return Selection(
        ids=ids[:limit],
        from_filters=True,
        limit=limit,
        truncated=len(ids) > limit,
    )
