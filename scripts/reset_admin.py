"""Resetea la cuenta admin local: contraseña desde .env, desbloqueo e intentos a 0.

Uso (desde la raíz de ecolima-backend, con la DB levantada):
    python scripts/reset_admin.py
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

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@ecolima.pe")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminSeguro2026!")


async def reset_admin() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        result = await conn.execute(
            "UPDATE users SET hashed_password = $2, failed_login_count = 0, "
            "locked_until = NULL, is_active = true WHERE email = $1",
            ADMIN_EMAIL,
            get_password_hash(ADMIN_PASSWORD),
        )
        if result == "UPDATE 0":
            print(f"No existe {ADMIN_EMAIL}; corre primero los seeds.")
        else:
            print(f"Admin {ADMIN_EMAIL} reseteado. Password: la de ADMIN_PASSWORD en .env")

        rows = await conn.fetch(
            "SELECT email, role, is_active, failed_login_count, locked_until FROM users ORDER BY user_id"
        )
        print("\nUsuarios en la DB:")
        for r in rows:
            print(f"  {r['email']:30} rol={r['role']:10} activo={r['is_active']} "
                  f"fallos={r['failed_login_count']} bloqueo={r['locked_until']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(reset_admin())
