"""Valida e importa el paquete de resultados versionado al esquema `analisis`.

Uso (desde la raíz de ecolima-backend, con la base migrada a head):
    python scripts/importar_paquete_analisis.py RUTA_PAQUETE              # valida, importa y activa
    python scripts/importar_paquete_analisis.py RUTA_PAQUETE --solo-validar
    python scripts/importar_paquete_analisis.py RUTA_PAQUETE --no-activar
    python scripts/importar_paquete_analisis.py --activar-version 1.0.0
    python scripts/importar_paquete_analisis.py --listar

RUTA_PAQUETE es el directorio que contiene `manifest.json`.

Por seguridad solo escribe en una base de esta máquina (localhost). Para una base
remota (despliegue) hace falta `--permitir-remoto`, con respaldo previo y
aprobación del responsable. La URL de la base nunca se imprime.
"""

import argparse
import asyncio
import sys
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.db_guard import motivo_rechazo_bd_local  # noqa: E402
from app.services.analisis_importador import activar_version, importar_paquete, listar_versiones  # noqa: E402
from app.services.analisis_paquete import ErrorPaquete, validar  # noqa: E402

_LIMA = timezone(timedelta(hours=-5))


def _solo_validar(ruta: str) -> int:
    informe = validar(ruta)
    for r in informe.resultados:
        print(f"[{'OK ' if r.ok else 'FALLA'}] {r.n:>2} {r.regla}")
        for e in r.errores[:5]:
            print(f"         - {e}")
    print("Paquete válido." if informe.ok else "Paquete RECHAZADO.")
    return 0 if informe.ok else 1


async def _ejecutar(args: argparse.Namespace, database_url: str) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            if args.listar:
                for v in await listar_versiones(session):
                    importado = v["importado_en"].astimezone(_LIMA)
                    print(f"{v['paquete_version']:>10}  id={v['version_id']}  activa={v['activa']}  "
                          f"manifiesto={v['manifest']}…  importado={importado:%Y-%m-%d %H:%M %z}")
                return 0
            if args.activar_version:
                version_id = await activar_version(session, args.activar_version)
                print(f"Versión {args.activar_version} activa (id={version_id}).")
                return 0
            r = await importar_paquete(session, args.ruta, activar=not args.no_activar)
            print(f"Paquete {r.paquete_version}: {r.estado} (id={r.version_id}, activa={r.activa}).")
            for tabla, n in sorted(r.conteos.items()):
                print(f"  {tabla:<18} {n:>6}")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ruta", nargs="?", help="directorio del paquete (contiene manifest.json)")
    parser.add_argument("--solo-validar", action="store_true", help="valida sin conectarse a la base")
    parser.add_argument("--no-activar", action="store_true", help="importa sin cambiar la versión activa")
    parser.add_argument("--activar-version", metavar="VERSION", help="activa una versión ya importada")
    parser.add_argument("--listar", action="store_true", help="lista las versiones importadas")
    parser.add_argument("--permitir-remoto", action="store_true",
                        help="permite escribir en una base que no es local (solo despliegue aprobado)")
    args = parser.parse_args()

    if not (args.ruta or args.activar_version or args.listar):
        parser.error("indique RUTA_PAQUETE, --activar-version o --listar")
    if args.solo_validar:
        if not args.ruta:
            parser.error("--solo-validar requiere RUTA_PAQUETE")
        return _solo_validar(args.ruta)

    database_url = Settings().database_url
    motivo = motivo_rechazo_bd_local(database_url)
    if motivo and not args.permitir_remoto:
        print(f"ERROR: no se escribe en esta base: {motivo}. Use --permitir-remoto solo en un despliegue "
              "aprobado y con respaldo.", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_ejecutar(args, database_url))
    except ErrorPaquete as ex:
        print(f"ERROR: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
