"""código de entrada no site (gerado pelo admin, com período)

Revisão: 0003
Anterior: 0002
Criada em: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "codigo_entrada",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("codigo_hash", sa.String(length=64), nullable=False),
        sa.Column("final", sa.String(length=4), nullable=False),
        sa.Column("rotulo", sa.String(length=80), nullable=True),
        sa.Column("inicio_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revogado", sa.Boolean(), nullable=False),
        sa.Column("usos", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("codigo_entrada", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_codigo_entrada_codigo_hash"), ["codigo_hash"], unique=True
        )
        batch_op.create_index(batch_op.f("ix_codigo_entrada_fim_em"), ["fim_em"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("codigo_entrada", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_codigo_entrada_fim_em"))
        batch_op.drop_index(batch_op.f("ix_codigo_entrada_codigo_hash"))
    op.drop_table("codigo_entrada")
