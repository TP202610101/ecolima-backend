import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from main import app
from app.core.config import Settings
from app.db.session import get_session


def _make_test_session_factory():
    """Engine con NullPool: cada request crea y destruye su propia conexión asyncpg.
    Evita errores 'another operation is in progress' entre tests secuenciales."""
    settings = Settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool, future=True)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


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
