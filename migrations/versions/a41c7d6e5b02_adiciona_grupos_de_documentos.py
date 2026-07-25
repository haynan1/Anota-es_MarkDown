"""adiciona grupos de documentos

Revision ID: a41c7d6e5b02
Revises: f0bd4a822918
Create Date: 2026-07-25 14:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a41c7d6e5b02'
down_revision = 'f0bd4a822918'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('slug', sa.String(length=110), nullable=False),
        sa.Column('description', sa.String(length=280), nullable=False),
        sa.Column('color', sa.String(length=9), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_groups_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_groups_name'), ['name'], unique=True)
        batch_op.create_index(batch_op.f('ix_groups_slug'), ['slug'], unique=True)

    op.create_table(
        'document_groups',
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('document_id', 'group_id'),
    )
    with op.batch_alter_table('document_groups', schema=None) as batch_op:
        batch_op.create_index('ix_document_groups_group_id', ['group_id'], unique=False)
        batch_op.create_index('ix_document_groups_order', ['group_id', 'position'], unique=False)


def downgrade():
    with op.batch_alter_table('document_groups', schema=None) as batch_op:
        batch_op.drop_index('ix_document_groups_order')
        batch_op.drop_index('ix_document_groups_group_id')
    op.drop_table('document_groups')

    with op.batch_alter_table('groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_groups_slug'))
        batch_op.drop_index(batch_op.f('ix_groups_name'))
        batch_op.drop_index(batch_op.f('ix_groups_uuid'))
    op.drop_table('groups')
