import os
import asyncio
from dotenv import load_dotenv
import asyncpg
from app.core.security import get_password_hash

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5433/ecolima")
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@ecolima.pe")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminSeguro2026!")

async def seed_admin():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        hashed_password = get_password_hash(ADMIN_PASSWORD)
        await conn.execute(
            "INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at) "
            "VALUES ($1, $2, $3, $4, true, NOW()) "
            "ON CONFLICT (email) DO NOTHING",
            ADMIN_EMAIL,
            hashed_password,
            "Administrador",
            "admin",
        )
        print(f"Seeded admin user {ADMIN_EMAIL}.")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(seed_admin())


if __name__ == "__main__":
    main()
