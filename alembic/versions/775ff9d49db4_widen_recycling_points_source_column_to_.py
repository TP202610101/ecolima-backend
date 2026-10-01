"""widen recycling_points source column to 150 chars

Revision ID: 775ff9d49db4
Revises: c9c50adf54a3
Create Date: 2026-10-01 11:45:31.093991

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '775ff9d49db4'
down_revision: Union[str, None] = 'c9c50adf54a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # VARCHAR(50) hacía fallar (StringDataRightTruncationError) el commit de
    # cualquier dataset cuyo "source" fuera más largo -- en la práctica, todo
    # el formato "Real - [Distrito] - [nivel] - [fuente]" usado para puntos
    # reales (ej. "Real - Santiago de Surco - prensa_verificar - Perú21
    # (comunicado Municipalidad de Surco)", 88 caracteres). 150 da margen
    # cómodo sin acercarse de nuevo al límite.
    op.alter_column(
        "recycling_points",
        "source",
        existing_type=sa.String(length=50),
        type_=sa.String(length=150),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "recycling_points",
        "source",
        existing_type=sa.String(length=150),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
