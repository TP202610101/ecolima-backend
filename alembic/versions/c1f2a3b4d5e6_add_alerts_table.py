"""add alerts table

Revision ID: c1f2a3b4d5e6
Revises: ba6e79de5c7a
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1f2a3b4d5e6'
down_revision: Union[str, None] = 'ba6e79de5c7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alerts',
        sa.Column('alert_id',       sa.Integer(),   autoincrement=True, nullable=False),
        sa.Column('district_id',    sa.Integer(),   nullable=False),
        sa.Column('saturation_pct', sa.Float(),     nullable=False),
        sa.Column('status',         sa.String(10),  nullable=False),
        sa.Column('created_at',     sa.DateTime(),  nullable=True),
        sa.Column('resolved_at',    sa.DateTime(),  nullable=True),
        sa.Column('resolved_by',    sa.Integer(),   nullable=True),
        sa.ForeignKeyConstraint(['district_id'], ['districts.district_id']),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.user_id']),
        sa.PrimaryKeyConstraint('alert_id'),
    )
    op.create_index(op.f('ix_alerts_district_id'), 'alerts', ['district_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_alerts_district_id'), table_name='alerts')
    op.drop_table('alerts')
