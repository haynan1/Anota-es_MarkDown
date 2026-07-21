"""Model package.

Importing this module registers every mapper, which Alembic autogenerate and
``db.create_all()`` both rely on.
"""

from app.models.category import Category
from app.models.document import (
    PAGE_SIZE_LABELS,
    PAGE_SIZES,
    PDF_THEME_LABELS,
    PDF_THEMES,
    Document,
)
from app.models.document_version import DocumentVersion
from app.models.setting import Setting
from app.models.tag import Tag, document_tags

__all__ = [
    "Category",
    "Document",
    "DocumentVersion",
    "Setting",
    "Tag",
    "document_tags",
    "PAGE_SIZES",
    "PAGE_SIZE_LABELS",
    "PDF_THEMES",
    "PDF_THEME_LABELS",
]
