"""uma conexão só

Revision ID: f3d9e0a71b62
Revises: d4a71f60c8e3
Create Date: 2026-09-02 17:00:00.000000

O quadro desenhava dois tipos de linha e nada na tela dizia qual era qual. Um
era a espinha - decidia o nível, o arranjo, o painel Estrutura e o Markdown
exportado; o outro atravessava o mapa sem mexer em nada. Qual deles um gesto
produzia virou a fonte mais confiável de confusão do mapa.

Um mapa mental é uma árvore. Passa a ter uma linha só, e essa linha é a
árvore.

Irreversível na prática: o `downgrade` recria a tabela vazia, porque as linhas
que existiam não têm para onde voltar - a informação que elas carregavam
(rótulo, traço, cor) não cabe em lugar nenhum do schema que fica. Por isso o
snapshot que a aplicação tira antes de migrar é o caminho de volta de verdade.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f3d9e0a71b62'
down_revision = 'd4a71f60c8e3'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('mind_map_edges')


def downgrade():
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
    # Os mesmos índices que a migração original criou: descer mais um degrau
    # depois desta tenta derrubá-los pelo nome, e um que não voltou aqui é uma
    # cadeia de migrações que não desce até o começo.
    with op.batch_alter_table('mind_map_edges', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mind_map_edges_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_mind_map_edges_source_id'), ['source_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mind_map_edges_target_id'), ['target_id'], unique=False)
        batch_op.create_index('ix_mind_map_edges_map', ['map_id'], unique=False)
