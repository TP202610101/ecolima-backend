"""add auth security columns to users

Revision ID: d2f3a4b5c6e7
Revises: c1f2a3b4d5e6
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd2f3a4b5c6e7'
down_revision: Union[str, None] = 'c1f2a3b4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_login_count')
    op.drop_column('users', 'token_version')
