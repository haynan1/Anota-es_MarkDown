"""adiciona metas, conquistas e frases

Revision ID: e18a4c73d905
Revises: c9e2f74a1d38
Create Date: 2026-09-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e18a4c73d905'
down_revision = 'c9e2f74a1d38'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'goals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('link_url', sa.String(length=500), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('time', sa.Time(), nullable=True),
        sa.Column('has_deadline', sa.Boolean(), nullable=False),
        sa.Column('show_on_board', sa.Boolean(), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recurrence_type', sa.String(length=15), nullable=False),
        sa.Column('recurrence_days', sa.Integer(), nullable=True),
        sa.Column('recurrence_end_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        # SET NULL: perder o documento tira o atalho, nunca o compromisso.
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('goals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_goals_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_goals_date'), ['date'], unique=False)
        batch_op.create_index(batch_op.f('ix_goals_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_goals_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_goals_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index('ix_goals_window', ['has_deadline', 'date'], unique=False)
        batch_op.create_index('ix_goals_status_date', ['status', 'date'], unique=False)

    op.create_table(
        'goal_occurrences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goal_id', sa.Integer(), nullable=False),
        sa.Column('occurrence_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('goal_id', 'occurrence_date', name='uq_goal_occurrence_day'),
    )
    with op.batch_alter_table('goal_occurrences', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_goal_occurrences_goal_id'), ['goal_id'], unique=False)
        batch_op.create_index(
            'ix_goal_occurrences_status_date', ['status', 'occurrence_date'], unique=False
        )

    op.create_table(
        'goal_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('link_url', sa.String(length=500), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('time', sa.Time(), nullable=True),
        sa.Column('show_on_board', sa.Boolean(), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('goal_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_goal_templates_uuid'), ['uuid'], unique=True)
        batch_op.create_index(
            batch_op.f('ix_goal_templates_document_id'), ['document_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_goal_templates_created_at'), ['created_at'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_goal_templates_updated_at'), ['updated_at'], unique=False
        )

    op.create_table(
        'achievement_unlocks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('unlocked_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('achievement_unlocks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_achievement_unlocks_key'), ['key'], unique=True)

    op.create_table(
        'motivational_phrases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('text', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('motivational_phrases', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_motivational_phrases_uuid'), ['uuid'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_motivational_phrases_created_at'), ['created_at'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_motivational_phrases_updated_at'), ['updated_at'], unique=False
        )


def downgrade():
    op.drop_table('motivational_phrases')
    op.drop_table('achievement_unlocks')
    op.drop_table('goal_templates')
    op.drop_table('goal_occurrences')
    op.drop_table('goals')
