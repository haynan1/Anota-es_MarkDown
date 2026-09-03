"""o centro segue a cor do mapa

Revision ID: c9e2f74a1d38
Revises: b5c1e4d9a730
Create Date: 2026-09-03 10:40:00.000000

Um mapa nascia com a própria cor *copiada* para dentro do tópico central. A
cópia é o que fazia "cor predominante" não predominar: trocada a cor do mapa
depois, o centro continuava com a cor da fundação, porque uma cor gravada num
nó é uma escolha daquele nó e ganha do acento.

O código parou de copiar. Esta migração desfaz as cópias que já existem, e
desfaz só as que são cópias: apenas o tópico central de cada mapa, e apenas
quando a cor dele é exatamente a do mapa. Um centro que alguém pintou de outra
cor de propósito é uma escolha, e escolha não se apaga.

Sem ``downgrade`` real. A informação que a subida descarta - "esta cor foi
digitada ou foi herdada?" - não existe mais depois dela, e uma descida que
chutasse a resposta pintaria de acento centros que ninguém pintou. Voltar é
seguro e não faz nada: o esquema não muda, e a versão antiga do código lê um
centro sem cor exatamente como lê qualquer nó sem cor.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9e2f74a1d38"
down_revision = "b5c1e4d9a730"
branch_labels = None
depends_on = None


# O centro é o primeiro tópico sem pai de cada mapa - o que nasceu com ele.
# "Primeiro" pela mesma chave que a tela usa: a posição, e o id para desempatar.
_CENTRES = """
    SELECT n.id
      FROM mind_map_nodes AS n
      JOIN mind_maps AS m ON m.id = n.map_id
     WHERE n.parent_id IS NULL
       AND n.color = m.color
       AND n.id = (
             SELECT MIN(inner_node.id)
               FROM mind_map_nodes AS inner_node
              WHERE inner_node.map_id = n.map_id
                AND inner_node.parent_id IS NULL
                AND inner_node.position = (
                      SELECT MIN(p.position)
                        FROM mind_map_nodes AS p
                       WHERE p.map_id = n.map_id
                         AND p.parent_id IS NULL
                    )
           )
"""


def upgrade():
    op.execute(
        sa.text(
            f"UPDATE mind_map_nodes SET color = '' WHERE id IN ({_CENTRES})"
        )
    )


def downgrade():
    """Nada a fazer - ver o cabeçalho."""
