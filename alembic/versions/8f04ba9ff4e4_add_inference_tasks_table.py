"""add inference_tasks table

Revision ID: 8f04ba9ff4e4
Revises: e3a4b5c6d7f8
Create Date: 2026-08-07 18:43:51.727340

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f04ba9ff4e4'
down_revision: Union[str, None] = 'e3a4b5c6d7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'inference_tasks',
        sa.Column('task_id',          sa.String(36),  nullable=False),
        sa.Column('status',           sa.String(20),  nullable=False, server_default='running'),
        sa.Column('progress_pct',     sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('zones_processed',  sa.Integer(),   nullable=False, server_default='0'),
        # JSON: {"high_priority": N, "medium_priority": N, "low_priority": N} si status='done',
        # o {"error": "mensaje"} si status='error'. NULL mientras status='running'.
        sa.Column('result_json',      sa.Text(),      nullable=True),
        sa.Column('created_at',       sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at',       sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('task_id'),
    )


def downgrade() -> None:
    op.drop_table('inference_tasks')
