"""Borra tareas de inferencia (inference_tasks) terminadas (done/error) hace
más de N días. No corre automáticamente -- no hay cron configurado en Azure
App Service; ejecutar manualmente o programarlo externamente (ej. Azure
WebJob) si la tabla crece demasiado.

Uso (desde la raíz de ecolima-backend, con la DB levantada):
    python scripts/cleanup_inference_tasks.py [dias=30]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import AsyncSessionLocal
from app.services.ml_service import delete_old_inference_tasks


async def main(days: int) -> None:
    async with AsyncSessionLocal() as db:
        deleted = await delete_old_inference_tasks(db, older_than_days=days)
    print(f"Borradas {deleted} tareas de inferencia (done/error) de más de {days} días.")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    asyncio.run(main(days))
