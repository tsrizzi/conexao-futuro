"""tokens no evento agregado (custo por turma no painel)

Revisão: 0002
Anterior: 0001
Criada em: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evento_agregado", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tokens_entrada", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("tokens_saida", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_evento_agregado_tipo"), ["tipo"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("evento_agregado", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_evento_agregado_tipo"))
        batch_op.drop_column("tokens_saida")
        batch_op.drop_column("tokens_entrada")
