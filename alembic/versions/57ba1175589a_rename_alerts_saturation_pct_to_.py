"""rename alerts.saturation_pct to redundancy_pct

Revision ID: 57ba1175589a
Revises: 8f04ba9ff4e4
Create Date: 2026-08-09 19:48:40.284416

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57ba1175589a'
down_revision: Union[str, None] = '8f04ba9ff4e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('alerts', 'saturation_pct', new_column_name='redundancy_pct')


def downgrade() -> None:
    op.alter_column('alerts', 'redundancy_pct', new_column_name='saturation_pct')
