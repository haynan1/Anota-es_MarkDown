"""adiciona cadeado de protecao contra exclusao

Revision ID: 1b9f208ac393
Revises: 7b3c256cc93b
Create Date: 2026-07-21 00:49:41.275276

Autogenerate also proposed dropping ``documents_fts`` and its shadow tables.
Those belong to the SQLite FTS5 virtual table, which lives outside the ORM
metadata by design - dropping them would wipe the search index on every
upgrade. The drops were removed here, and ``migrations/env.py`` now filters
those names out so the suggestion cannot come back.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b9f208ac393'
down_revision = '7b3c256cc93b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_locked', sa.Boolean(), server_default='0', nullable=False)
        )


def downgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('is_locked')
