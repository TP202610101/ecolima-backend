"""Crea la cuenta de analista para pruebas locales.

Uso (desde la raíz de ecolima-backend, con la DB levantada):
    python scripts/create_analista.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
from dotenv import load_dotenv

from app.core.security import get_password_hash

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5433/ecolima")
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

EMAIL = "ana@analista.com"
PASSWORD = "analista123@"
FULL_NAME = "Ana Analista"
ROLE = "analista"


async def create_analista() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        result = await conn.execute(
            "INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at) "
            "VALUES ($1, $2, $3, $4, true, NOW()) "
            "ON CONFLICT (email) DO UPDATE SET hashed_password = $2, role = $4, is_active = true",
            EMAIL,
            get_password_hash(PASSWORD),
            FULL_NAME,
            ROLE,
        )
        print(f"Usuario {EMAIL} (rol {ROLE}) creado/actualizado. [{result}]")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(create_analista())
