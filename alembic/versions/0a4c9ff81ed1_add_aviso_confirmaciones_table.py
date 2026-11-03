"""add aviso_confirmaciones table

Revision ID: 0a4c9ff81ed1
Revises: c5475d62af58
Create Date: 2026-11-02 21:14:48.681217

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a4c9ff81ed1'
down_revision: Union[str, None] = 'c5475d62af58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'aviso_confirmaciones',
        sa.Column('confirmacion_id', sa.Integer(),    autoincrement=True, nullable=False),
        sa.Column('user_id',         sa.Integer(),    nullable=False),
        # Identifica el aviso (el contenido vive en el frontend, acá solo se
        # referencia). Ej.: "fuentes-limitaciones".
        sa.Column('clave',           sa.String(50),   nullable=False),
        # String, no int -- misma razón que model_versions.version_name: no
        # hay garantía de que siempre sea numérica (podría ser "1", "1.1", etc.).
        sa.Column('version',         sa.String(20),   nullable=False),
        sa.Column('confirmado_en',   sa.DateTime(),   nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('confirmacion_id'),
        # Una confirmación por usuario, clave y versión -- repetir la misma
        # confirmación debe ser idempotente, no crear filas duplicadas.
        sa.UniqueConstraint('user_id', 'clave', 'version', name='uq_aviso_confirmaciones_user_clave_version'),
    )
    op.create_index(
        op.f('ix_aviso_confirmaciones_user_id'),
        'aviso_confirmaciones',
        ['user_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_aviso_confirmaciones_user_id'), table_name='aviso_confirmaciones')
    op.drop_table('aviso_confirmaciones')
