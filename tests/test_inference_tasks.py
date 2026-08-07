"""
tests/test_inference_tasks.py
Persistencia del estado de tareas de inferencia ML en Postgres (tabla
inference_tasks), en reemplazo del dict en memoria (_inference_tasks) que se
perdía en cada reinicio del worker gunicorn.
"""
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inference_task import InferenceTask
from app.services.ml_service import (
    ORPHAN_TASK_TIMEOUT_MINUTES,
    _finish_inference_task,
    create_inference_task,
    get_inference_status,
)
from tests.conftest import _make_test_session_factory, auth_headers

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def cleanup_inference_tasks(db_session: AsyncSession):
    """Red de seguridad análoga a cleanup_datasets -- borra las tareas que el test cree."""
    created_ids: list[str] = []
    yield created_ids
    for task_id in created_ids:
        await db_session.execute(delete(InferenceTask).where(InferenceTask.task_id == task_id))
    await db_session.commit()


def _new_task_id() -> str:
    # Mismo formato que el task_id real (str(uuid.uuid4()), 36 chars) --
    # la columna es String(36), un prefijo tipo "pytest-" la desbordaría.
    return str(uuid.uuid4())


async def test_create_inference_task_appears_with_running_status(
    db_session: AsyncSession, cleanup_inference_tasks: list[str]
):
    task_id = _new_task_id()
    cleanup_inference_tasks.append(task_id)

    await create_inference_task(db_session, task_id)

    result = await get_inference_status(db_session, task_id)
    assert result == {"status": "running", "progress_pct": 0, "zones_processed": 0}


async def test_update_progress_is_reflected_in_the_row(
    db_session: AsyncSession, cleanup_inference_tasks: list[str]
):
    task_id = _new_task_id()
    cleanup_inference_tasks.append(task_id)
    await create_inference_task(db_session, task_id)

    # Misma forma de UPDATE que usa el closure _update_task dentro de run_inference.
    await db_session.execute(
        update(InferenceTask)
        .where(InferenceTask.task_id == task_id)
        .values(progress_pct=55, zones_processed=120, updated_at=datetime.utcnow())
    )
    await db_session.commit()

    result = await get_inference_status(db_session, task_id)
    assert result["status"] == "running"
    assert result["progress_pct"] == 55
    assert result["zones_processed"] == 120


async def test_finish_task_success_sets_done_status_and_result(
    db_session: AsyncSession, cleanup_inference_tasks: list[str]
):
    task_id = _new_task_id()
    cleanup_inference_tasks.append(task_id)
    await create_inference_task(db_session, task_id)

    await _finish_inference_task(
        db_session, task_id, task_status="done", progress_pct=100, zones_processed=50,
        result={"zones_processed": 50, "high_priority": 5, "medium_priority": 10, "low_priority": 35},
    )

    result = await get_inference_status(db_session, task_id)
    assert result == {
        "status": "done",
        "progress_pct": 100,
        "zones_processed": 50,
        "high_priority": 5,
        "medium_priority": 10,
        "low_priority": 35,
    }


async def test_finish_task_error_sets_error_status_and_message(
    db_session: AsyncSession, cleanup_inference_tasks: list[str]
):
    task_id = _new_task_id()
    cleanup_inference_tasks.append(task_id)
    await create_inference_task(db_session, task_id)

    await _finish_inference_task(
        db_session, task_id, task_status="error", progress_pct=0, zones_processed=0,
        error="No hay modelo LightGBM disponible.",
    )

    result = await get_inference_status(db_session, task_id)
    assert result["status"] == "error"
    assert result["error"] == "No hay modelo LightGBM disponible."


async def test_missing_task_id_raises_404(db_session: AsyncSession):
    with pytest.raises(HTTPException) as exc_info:
        await get_inference_status(db_session, "no-existe-este-task-id")
    assert exc_info.value.status_code == 404


async def test_orphaned_running_task_is_marked_as_error_on_read(
    db_session: AsyncSession, cleanup_inference_tasks: list[str]
):
    """Una tarea 'running' sin actualizaciones por más de ORPHAN_TASK_TIMEOUT_MINUTES
    (worker reiniciado a mitad de la inferencia) se reporta como 'error', no
    'running' para siempre."""
    task_id = _new_task_id()
    cleanup_inference_tasks.append(task_id)
    await create_inference_task(db_session, task_id)

    stale_time = datetime.utcnow() - timedelta(minutes=ORPHAN_TASK_TIMEOUT_MINUTES + 1)
    await db_session.execute(
        update(InferenceTask).where(InferenceTask.task_id == task_id).values(updated_at=stale_time)
    )
    await db_session.commit()

    result = await get_inference_status(db_session, task_id)

    assert result["status"] == "error"
    assert "interrumpida" in result["error"]


async def test_recent_running_task_is_not_treated_as_orphaned(
    db_session: AsyncSession, cleanup_inference_tasks: list[str]
):
    task_id = _new_task_id()
    cleanup_inference_tasks.append(task_id)
    await create_inference_task(db_session, task_id)

    result = await get_inference_status(db_session, task_id)

    assert result["status"] == "running"


async def test_task_state_survives_a_new_db_session_simulating_worker_restart():
    """Crea la tarea con una sesión/engine, los descarta por completo (nada
    queda en memoria del proceso), y la lee con una sesión/engine totalmente
    nueva -- así es como se comportaría un segundo worker de gunicorn que
    nunca vio la tarea 'en RAM'."""
    task_id = _new_task_id()

    SessionA, engine_a = _make_test_session_factory()
    async with SessionA() as db_a:
        await create_inference_task(db_a, task_id)
    await engine_a.dispose()

    SessionB, engine_b = _make_test_session_factory()
    try:
        async with SessionB() as db_b:
            result = await get_inference_status(db_b, task_id)
            assert result["status"] == "running"

            await db_b.execute(delete(InferenceTask).where(InferenceTask.task_id == task_id))
            await db_b.commit()
    finally:
        await engine_b.dispose()


async def test_inference_status_endpoint_returns_404_for_unknown_task(
    client: AsyncClient, admin_token: str
):
    resp = await client.get(
        "/api/v1/ml/inference-status/no-existe-este-task-id",
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 404
