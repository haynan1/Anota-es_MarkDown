"""um mapa pode ser travado

Revision ID: b5c1e4d9a730
Revises: a7b4c1e93f28
Create Date: 2026-09-03 09:10:00.000000

O cadeado do mapa não é o cadeado do documento. Um documento travado ainda se
edita e só resiste à exclusão; um mapa travado fica somente leitura inteiro,
porque o acidente que ele existe para impedir é uma tecla numa tela, não um
clique numa lixeira.

``server_default='0'`` e não apenas ``default``: a coluna entra ``NOT NULL``
num banco que já tem mapas, e sem um padrão do lado do servidor as linhas
existentes não teriam valor nenhum para receber.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b5c1e4d9a730"
down_revision = "a7b4c1e93f28"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mind_maps", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_locked", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade():
    with op.batch_alter_table("mind_maps", schema=None) as batch_op:
        batch_op.drop_column("is_locked")
