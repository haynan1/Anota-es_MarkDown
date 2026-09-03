"""Model package.

Importing this module registers every mapper, which Alembic autogenerate and
``db.create_all()`` both rely on.
"""

from app.models.achievement import AchievementUnlock
from app.models.category import Category
from app.models.document import (
    PAGE_SIZE_LABELS,
    PAGE_SIZES,
    PDF_THEME_LABELS,
    PDF_THEMES,
    Document,
)
from app.models.document_version import DocumentVersion
from app.models.goal import (
    ACTIVE_STATUSES,
    CATEGORY_ICONS,
    CATEGORY_LABELS,
    GOAL_CATEGORIES,
    GOAL_PRIORITIES,
    GOAL_STATUSES,
    PRIORITY_LABELS,
    RECURRENCE_LABELS,
    RECURRENCE_TYPES,
    STATUS_LABELS,
    Goal,
    GoalOccurrence,
    GoalTemplate,
)
from app.models.group import Group, document_groups
from app.models.media_asset import MediaAsset
from app.models.mind_map import (
    LAYOUT_LABELS,
    LAYOUTS,
    NODE_KINDS,
    NODE_SHAPES,
    MindMap,
    MindMapNode,
)
from app.models.phrase import MotivationalPhrase
from app.models.setting import Setting
from app.models.tag import Tag, document_tags

__all__ = [
    "AchievementUnlock",
    "Category",
    "Document",
    "DocumentVersion",
    "Goal",
    "GoalOccurrence",
    "GoalTemplate",
    "Group",
    "MotivationalPhrase",
    "MediaAsset",
    "MindMap",
    "MindMapNode",
    "Setting",
    "Tag",
    "document_groups",
    "document_tags",
    "PAGE_SIZES",
    "PAGE_SIZE_LABELS",
    "PDF_THEMES",
    "PDF_THEME_LABELS",
    "NODE_KINDS",
    "NODE_SHAPES",
    "LAYOUTS",
    "LAYOUT_LABELS",
    "ACTIVE_STATUSES",
    "CATEGORY_ICONS",
    "CATEGORY_LABELS",
    "GOAL_CATEGORIES",
    "GOAL_PRIORITIES",
    "GOAL_STATUSES",
    "PRIORITY_LABELS",
    "RECURRENCE_LABELS",
    "RECURRENCE_TYPES",
    "STATUS_LABELS",
]
