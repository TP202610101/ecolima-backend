from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from main import app
from app.core.config import Settings
from app.db.session import get_session

# Prefijo obligatorio para cualquier archivo subido por un test. Permite
# identificar y limpiar datasets de prueba, incluidos huérfanos dejados por
# corridas anteriores interrumpidas (crash, Ctrl+C) que no llegaron a hacer
# teardown.
TEST_FILE_PREFIX = "pytest_test_"

UPLOAD_DIR = Path("uploads/datasets")


def _make_test_session_factory():
    """Engine con NullPool: cada request crea y destruye su propia conexión asyncpg.
    Evita errores 'another operation is in progress' entre tests secuenciales."""
    settings = Settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool, future=True)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _delete_dataset(session: AsyncSession, dataset_id: int) -> None:
    """Borra el archivo en disco (si existe) y el registro en `datasets`."""
    for path in UPLOAD_DIR.glob(f"{dataset_id}.*"):
        path.unlink(missing_ok=True)
    await session.execute(text("DELETE FROM datasets WHERE dataset_id = :id"), {"id": dataset_id})


@pytest_asyncio.fixture
async def client():
    """Cliente HTTP async con NullPool engine — aislamiento completo entre tests."""
    TestSession, engine = _make_test_session_factory()

    async def _override_get_session():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=True,
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> str:
    """JWT del usuario admin seed (admin@ecolima.pe)."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@ecolima.pe", "password": "AdminSeguro2026!"},
    )
    assert resp.status_code == 200, f"Login falló: {resp.text}"
    return resp.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def db_session():
    """Sesión de BD directa (fuera del cliente HTTP), solo para setup/cleanup de tests."""
    TestSession, engine = _make_test_session_factory()
    async with TestSession() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def cleanup_datasets(db_session: AsyncSession):
    """
    Red de seguridad para que los tests que crean `datasets` no dejen basura
    en la BD de Neon (crítico #1 del inventario de faltantes).

    Uso: el test hace `cleanup_datasets.append(dataset_id)` con el id
    devuelto por el endpoint de upload. Al terminar el test (pase o falle)
    se borra ese registro y su archivo en `uploads/datasets/`.

    Además, como red de seguridad adicional, borra cualquier dataset huérfano
    de corridas anteriores interrumpidas, identificado por el prefijo
    TEST_FILE_PREFIX en `original_filename` — así un test que crashea antes
    de este teardown no deja basura permanente.
    """
    created_ids: list[int] = []

    yield created_ids

    for dataset_id in created_ids:
        await _delete_dataset(db_session, dataset_id)

    orphans = await db_session.execute(
        text("SELECT dataset_id FROM datasets WHERE original_filename LIKE :prefix"),
        {"prefix": f"{TEST_FILE_PREFIX}%"},
    )
    for row in orphans.fetchall():
        if row[0] not in created_ids:
            await _delete_dataset(db_session, row[0])

    await db_session.commit()
