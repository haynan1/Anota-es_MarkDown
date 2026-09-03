"""tópico compartilhado

Revision ID: a7b4c1e93f28
Revises: f3d9e0a71b62
Create Date: 2026-09-02 20:00:00.000000

Uma árvore não sabe dizer "isto vale para todas as etapas": um tópico tem um
pai. O espelho é como isso passa a ser dito sem que o mapa deixe de ser uma
árvore - uma linha como qualquer outra, com um tópico que mora noutro lugar na
ponta dela.

Aditiva e reversível: a coluna é nula em toda linha que existe hoje, e nenhum
mapa muda de forma ao subir.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7b4c1e93f28'
down_revision = 'f3d9e0a71b62'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mind_map_nodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mirror_of_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_mind_map_nodes_mirror_of_id'), ['mirror_of_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_mind_map_nodes_mirror', 'mind_map_nodes',
            ['mirror_of_id'], ['id'], ondelete='CASCADE',
        )


def downgrade():
    # Os espelhos vão junto com a coluna: sem ela um espelho é uma linha em
    # branco pendurada na árvore, e uma linha em branco é pior do que nada.
    op.execute(sa.text("DELETE FROM mind_map_nodes WHERE mirror_of_id IS NOT NULL"))
    with op.batch_alter_table('mind_map_nodes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mind_map_nodes_mirror_of_id'))
        batch_op.drop_column('mirror_of_id')
