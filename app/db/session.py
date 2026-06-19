from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings

settings = Settings()
engine = create_async_engine(
    settings.database_url,
    future=True,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
