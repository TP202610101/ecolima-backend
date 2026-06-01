"""add model_versions table

Revision ID: ba6e79de5c7a
Revises: 001_initial
Create Date: 2026-06-01 12:11:28.763615

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ba6e79de5c7a'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'model_versions',
        sa.Column('version_id',    sa.Integer(),   autoincrement=True, nullable=False),
        sa.Column('version_name',  sa.String(20),  nullable=False),
        sa.Column('training_date', sa.DateTime(),  nullable=True),
        sa.Column('trained_by',    sa.Integer(),   nullable=True),
        sa.Column('artifact_url',  sa.String(500), nullable=True),
        sa.Column('metrics',       sa.Text(),      nullable=True),
        sa.Column('features_used', sa.Text(),      nullable=True),
        sa.Column('is_active',     sa.Boolean(),   nullable=False),
        sa.Column('created_at',    sa.DateTime(),  nullable=True),
        sa.ForeignKeyConstraint(['trained_by'], ['users.user_id']),
        sa.PrimaryKeyConstraint('version_id'),
    )
    op.create_index(
        op.f('ix_model_versions_version_name'),
        'model_versions',
        ['version_name'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_model_versions_version_name'), table_name='model_versions')
    op.drop_table('model_versions')
