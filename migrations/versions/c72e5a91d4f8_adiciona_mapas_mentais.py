"""adiciona mapas mentais

Revision ID: c72e5a91d4f8
Revises: a41c7d6e5b02
Create Date: 2026-09-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c72e5a91d4f8'
down_revision = 'a41c7d6e5b02'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mind_maps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=140), nullable=False),
        sa.Column('slug', sa.String(length=160), nullable=False),
        sa.Column('description', sa.String(length=280), nullable=False),
        sa.Column('color', sa.String(length=9), nullable=False),
        sa.Column('layout', sa.String(length=10), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('viewport_x', sa.Float(), nullable=False),
        sa.Column('viewport_y', sa.Float(), nullable=False),
        sa.Column('viewport_zoom', sa.Float(), nullable=False),
        sa.Column('is_favorite', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mind_maps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mind_maps_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_mind_maps_slug'), ['slug'], unique=True)
        batch_op.create_index(batch_op.f('ix_mind_maps_title'), ['title'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_maps_is_favorite'), ['is_favorite'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_maps_is_deleted'), ['is_deleted'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_maps_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_maps_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index('ix_mind_maps_state_updated', ['is_deleted', 'updated_at'], unique=False)

    op.create_table(
        'mind_map_nodes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('map_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('text', sa.String(length=500), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('image_url', sa.String(length=500), nullable=False),
        sa.Column('media_asset_id', sa.Integer(), nullable=True),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('x', sa.Float(), nullable=False),
        sa.Column('y', sa.Float(), nullable=False),
        sa.Column('width', sa.Float(), nullable=False),
        sa.Column('height', sa.Float(), nullable=False),
        sa.Column('color', sa.String(length=9), nullable=False),
        sa.Column('shape', sa.String(length=12), nullable=False),
        sa.Column('is_collapsed', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['map_id'], ['mind_maps.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['mind_map_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['media_asset_id'], ['media_assets.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mind_map_nodes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mind_map_nodes_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_mind_map_nodes_map_id'), ['map_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_map_nodes_parent_id'), ['parent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_map_nodes_media_asset_id'), ['media_asset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_map_nodes_document_id'), ['document_id'], unique=False)
        batch_op.create_index('ix_mind_map_nodes_tree', ['map_id', 'parent_id', 'position'], unique=False)

    op.create_table(
        'mind_map_edges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('map_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('style', sa.String(length=10), nullable=False),
        sa.Column('color', sa.String(length=9), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['map_id'], ['mind_maps.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_id'], ['mind_map_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_id'], ['mind_map_nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_id', 'target_id', name='uq_mind_map_edge_pair'),
    )
    with op.batch_alter_table('mind_map_edges', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mind_map_edges_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_mind_map_edges_source_id'), ['source_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_map_edges_target_id'), ['target_id'], unique=False)
        batch_op.create_index('ix_mind_map_edges_map', ['map_id'], unique=False)


def downgrade():
    with op.batch_alter_table('mind_map_edges', schema=None) as batch_op:
        batch_op.drop_index('ix_mind_map_edges_map')
        batch_op.drop_index(batch_op.f('ix_mind_map_edges_target_id'))
        batch_op.drop_index(batch_op.f('ix_mind_map_edges_source_id'))
        batch_op.drop_index(batch_op.f('ix_mind_map_edges_uuid'))
    op.drop_table('mind_map_edges')

    with op.batch_alter_table('mind_map_nodes', schema=None) as batch_op:
        batch_op.drop_index('ix_mind_map_nodes_tree')
        batch_op.drop_index(batch_op.f('ix_mind_map_nodes_document_id'))
        batch_op.drop_index(batch_op.f('ix_mind_map_nodes_media_asset_id'))
        batch_op.drop_index(batch_op.f('ix_mind_map_nodes_parent_id'))
        batch_op.drop_index(batch_op.f('ix_mind_map_nodes_map_id'))
        batch_op.drop_index(batch_op.f('ix_mind_map_nodes_uuid'))
    op.drop_table('mind_map_nodes')

    with op.batch_alter_table('mind_maps', schema=None) as batch_op:
        batch_op.drop_index('ix_mind_maps_state_updated')
        batch_op.drop_index(batch_op.f('ix_mind_maps_updated_at'))
        batch_op.drop_index(batch_op.f('ix_mind_maps_created_at'))
        batch_op.drop_index(batch_op.f('ix_mind_maps_is_deleted'))
        batch_op.drop_index(batch_op.f('ix_mind_maps_is_favorite'))
        batch_op.drop_index(batch_op.f('ix_mind_maps_title'))
        batch_op.drop_index(batch_op.f('ix_mind_maps_slug'))
        batch_op.drop_index(batch_op.f('ix_mind_maps_uuid'))
    op.drop_table('mind_maps')
