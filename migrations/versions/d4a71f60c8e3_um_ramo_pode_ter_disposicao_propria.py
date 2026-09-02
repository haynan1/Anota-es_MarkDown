"""um ramo pode ter disposição própria

Revision ID: d4a71f60c8e3
Revises: c72e5a91d4f8
Create Date: 2026-09-02 12:00:00.000000

Nullable on purpose, and nullable is the whole design: ``NULL`` means "the
same as whatever this branch hangs from", which is what every node in every
existing map wants and what a new node should keep wanting. A default of
``'right'`` would have written an opinion onto ten thousand rows that never
expressed one, and changing a map's arrangement afterwards would have moved
nothing.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a71f60c8e3'
down_revision = 'c72e5a91d4f8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mind_map_nodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('layout', sa.String(length=10), nullable=True))


def downgrade():
    with op.batch_alter_table('mind_map_nodes', schema=None) as batch_op:
        batch_op.drop_column('layout')
