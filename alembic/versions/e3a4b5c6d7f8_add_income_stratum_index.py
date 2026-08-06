"""add index on candidate_zones.income_stratum

Revision ID: e3a4b5c6d7f8
Revises: d2f3a4b5c6e7
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e3a4b5c6d7f8'
down_revision: Union[str, None] = 'd2f3a4b5c6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_candidate_zones_income_stratum',
        'candidate_zones',
        ['income_stratum'],
    )


def downgrade() -> None:
    op.drop_index('ix_candidate_zones_income_stratum', table_name='candidate_zones')
