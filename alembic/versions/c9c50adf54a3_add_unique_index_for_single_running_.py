"""add unique index for single running inference task

Revision ID: c9c50adf54a3
Revises: 57ba1175589a
Create Date: 2026-09-23 20:05:28.631638

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9c50adf54a3'
down_revision: Union[str, None] = '57ba1175589a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Garantiza a nivel de BD que solo puede existir UNA fila de
    # inference_tasks con status='running' a la vez -- backstop real contra
    # el doble disparo de /ml/run-inference (auditoria de concurrencia,
    # hallazgo #4). Antes no había ninguna guardia; un chequeo a nivel de
    # aplicacion (SELECT antes de INSERT) por si solo seria racy igual que
    # el hallazgo del "ultimo admin", asi que la garantia real vive aca.
    op.execute(
        "CREATE UNIQUE INDEX ix_inference_tasks_single_running "
        "ON inference_tasks ((1)) WHERE status = 'running'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inference_tasks_single_running")
