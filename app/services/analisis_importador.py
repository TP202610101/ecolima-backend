"""Importación del paquete de resultados al esquema `analisis` (una transacción).

Flujo:
1. `validar_o_rechazar`: validación 0 y 1–15, fuera de la transacción. Si algo
   falla no se abre ninguna escritura.
2. En una sola transacción, con un candado de asesoría para que dos
   importaciones no se crucen:
   - si la `paquete_version` ya existe con el mismo manifiesto (sha256), no se
     vuelve a cargar (idempotente);
   - si existe con otro manifiesto, se rechaza: es un conflicto de contenido y
     el paquete debe publicarse con otra versión;
   - si no existe, se cargan las 14 tablas y se comprueba que los conteos de la
     base coincidan con el paquete y su manifiesto;
   - si se pide, la versión queda como la única activa.
3. Cualquier error revierte todo. Las tablas del prototipo (`public`) no se tocan.

El backend no recalcula nada: los valores se guardan tal cual vienen en el paquete.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analisis import (
    SCHEMA,
    ConcordanciaMcda,
    CriterioMcda,
    Fuente,
    Mcda,
    McdaMontecarlo,
    MetricaSemilla,
    ModeloAnalisis,
    PesoMcda,
    Propension,
    SitioFeature,
    Tamizaje,
    VersionAnalisis,
)
from app.services.analisis_paquete import COLUMNAS, ErrorPaquete, Informe, validar_o_rechazar

# Candado de asesoría (pg_advisory_xact_lock) que serializa importaciones y activaciones.
_CANDADO_IMPORTACION = 7_310_202_610

# CSV del paquete -> (modelo, columnas del CSV que no se guardan en esa tabla)
_TABLAS_CSV = [
    ("salidas/features.csv", SitioFeature, {"clase_oportunidad"}),  # ya está en `sitio`
    ("salidas/tamizaje.csv", Tamizaje, set()),
    ("salidas/propension.csv", Propension, {"rol"}),  # ya está en `sitio`; la validación 7 lo compara
    ("salidas/criterios_mcda.csv", CriterioMcda, set()),
    ("salidas/pesos_mcda.csv", PesoMcda, set()),
    ("salidas/mcda.csv", Mcda, set()),
    ("salidas/mcda_montecarlo.csv", McdaMontecarlo, set()),
    ("salidas/concordancia_mcda.csv", ConcordanciaMcda, set()),
    ("datos/fuentes.csv", Fuente, set()),
    ("modelo/metricas_por_semilla.csv", MetricaSemilla, set()),
]


class ErrorConflictoVersion(ErrorPaquete):
    """La versión ya está importada con otro contenido."""


class ErrorImportacion(ErrorPaquete):
    """La carga no dejó la base coherente con el paquete; se revirtió."""


@dataclass
class ResultadoImportacion:
    version_id: int
    paquete_version: str
    estado: str  # "importada" | "ya_importada"
    activa: bool
    conteos: dict[str, int] = field(default_factory=dict)


def _convertir(valor: str, tipo: str):
    if valor == "":
        return None
    if tipo == "r":
        return float(valor)
    if tipo == "i":
        return int(valor)
    if tipo == "b":
        return valor == "True"
    if tipo == "d":
        return date.fromisoformat(valor)
    return valor


def _filas(df: pd.DataFrame, ruta: str, excluir: set[str], version_id: int) -> list[dict]:
    """Filas del CSV con tipos de Python y columnas en minúsculas (nombres de la tabla)."""
    tipos = [(n, t) for n, t in COLUMNAS[ruta][0] if n not in excluir]
    return [
        {"version_id": version_id, **{n.lower(): _convertir(v, t) for (n, t), v in zip(tipos, valores)}}
        for valores in df[[n for n, _ in tipos]].itertuples(index=False, name=None)
    ]


async def _insertar_version(session: AsyncSession, informe: Informe, importado_por: int | None) -> int:
    m = informe.paquete.manifest
    return (await session.execute(
        insert(VersionAnalisis).values(
            paquete_version=m["paquete"],
            dataset_version=m["dataset"]["version"],
            dataset_sha256=m["dataset"]["sha256"],
            modelo_version=m["modelo"]["version"],
            mcda_version=m["mcda"]["version"],
            fecha_paquete=datetime.fromisoformat(m["fecha"]),
            descripcion=m["descripcion"],
            manifest_sha256=informe.paquete.manifest_sha256,
            manifest=m,
            activa=False,
            importado_por=importado_por,
        ).returning(VersionAnalisis.version_id)
    )).scalar_one()


async def _cargar_distritos(session: AsyncSession, informe: Informe, version_id: int) -> None:
    g = informe.lector.geojson()
    await session.execute(
        text(f"INSERT INTO {SCHEMA}.distrito (version_id, ubigeo, nombre, geometry) "
             "VALUES (:v, :u, :n, ST_SetSRID(ST_GeomFromGeoJSON(:g), 4326))"),
        [{"v": version_id, "u": f["properties"]["ubigeo"], "n": f["properties"]["nombre"],
          "g": json.dumps(f["geometry"])} for f in g["features"]],
    )


async def _cargar_sitios(session: AsyncSession, informe: Informe, version_id: int) -> None:
    filas = _filas(informe.lector.txt("salidas/sitios.csv"), "salidas/sitios.csv", set(), version_id)
    for f in filas:
        f["ubigeo"] = f"{f['ubigeo']:06d}"
    await session.execute(
        text(f"INSERT INTO {SCHEMA}.sitio (version_id, sitio_id, lon, lat, geom, ubigeo, distrito, "
             "clase_oportunidad, rol) VALUES (:version_id, :sitio_id, :lon, :lat, "
             "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :ubigeo, :distrito, :clase_oportunidad, :rol)"),
        filas,
    )


async def _cargar_tablas_csv(session: AsyncSession, informe: Informe, version_id: int) -> None:
    for ruta, modelo, excluir in _TABLAS_CSV:
        filas = _filas(informe.lector.txt(ruta), ruta, excluir, version_id)
        await session.execute(insert(modelo), filas)


async def _cargar_modelo(session: AsyncSession, informe: Informe, version_id: int) -> None:
    p, m = informe.paquete, informe.paquete.manifest
    await session.execute(insert(ModeloAnalisis).values(
        version_id=version_id,
        modelo_version=m["modelo"]["version"],
        algoritmo=m["modelo"]["algoritmo"],
        model_card=p.json("modelo/model_card.json"),
        parametros=p.json("modelo/logistica_parametros.json"),
        metricas_resumen=m["modelo"]["metricas_resumen"],
    ))


# Orden de carga: padres antes que hijos. Los tests pueden interceptar un paso para provocar un fallo.
_PASOS_CARGA = [_cargar_distritos, _cargar_sitios, _cargar_tablas_csv, _cargar_modelo]


def _conteos_esperados(informe: Informe) -> dict[str, int]:
    """Filas que debe tener cada tabla según el paquete; coinciden con `manifest.archivos[].filas`."""
    lector, m = informe.lector, informe.paquete.manifest
    filas_manifiesto = {a["ruta"]: a.get("filas") for a in m["archivos"]}
    esperados = {"distrito": len(lector.geojson()["features"]), "sitio": len(lector.txt("salidas/sitios.csv")),
                 "modelo": 1}
    for ruta, modelo, _ in _TABLAS_CSV:
        n = len(lector.txt(ruta))
        if filas_manifiesto.get(ruta) not in (None, n):
            raise ErrorImportacion(f"{ruta}: {n} filas ≠ {filas_manifiesto[ruta]} del manifiesto")
        esperados[modelo.__tablename__] = n
    return esperados


async def contar_filas(session: AsyncSession, version_id: int) -> dict[str, int]:
    tablas = [t.name for t in VersionAnalisis.metadata.sorted_tables
              if t.schema == SCHEMA and t.name != "version_analisis"]
    sql = " UNION ALL ".join(
        f"SELECT '{t}' AS tabla, count(*) AS n FROM {SCHEMA}.{t} WHERE version_id = :v" for t in tablas)
    return {r.tabla: r.n for r in (await session.execute(text(sql), {"v": version_id}))}


async def _activar(session: AsyncSession, version_id: int) -> None:
    # Primero se desactiva la anterior: el índice único parcial no es diferible.
    await session.execute(update(VersionAnalisis)
                          .where(VersionAnalisis.activa.is_(True), VersionAnalisis.version_id != version_id)
                          .values(activa=False))
    await session.execute(update(VersionAnalisis).where(VersionAnalisis.version_id == version_id).values(activa=True))


async def importar_paquete(
    session: AsyncSession,
    ruta: Path | str,
    *,
    activar: bool = True,
    importado_por: int | None = None,
) -> ResultadoImportacion:
    """Valida e importa un paquete. La sesión no debe tener una transacción abierta."""
    informe = validar_o_rechazar(ruta)
    m = informe.paquete.manifest
    esperados = _conteos_esperados(informe)

    async with session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CANDADO_IMPORTACION})
        existente = (await session.execute(
            select(VersionAnalisis).where(VersionAnalisis.paquete_version == m["paquete"])
        )).scalar_one_or_none()

        if existente is not None:
            if existente.manifest_sha256 != informe.paquete.manifest_sha256:
                raise ErrorConflictoVersion(
                    f"la versión {m['paquete']} ya está importada con otro contenido (manifiesto distinto); "
                    "publique el paquete con una versión nueva")
            version_id, estado = existente.version_id, "ya_importada"
        else:
            version_id, estado = await _insertar_version(session, informe, importado_por), "importada"
            for paso in _PASOS_CARGA:
                await paso(session, informe, version_id)

        conteos = await contar_filas(session, version_id)
        if conteos != esperados:
            diferencias = {t: (conteos.get(t), n) for t, n in esperados.items() if conteos.get(t) != n}
            raise ErrorImportacion(f"conteos en la base distintos del paquete (base, paquete): {diferencias}")

        if activar:
            await _activar(session, version_id)
        # Las FK diferidas se comprueban aquí, dentro de la transacción.
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        activa = (await session.execute(
            select(VersionAnalisis.activa).where(VersionAnalisis.version_id == version_id))).scalar_one()

    return ResultadoImportacion(version_id, m["paquete"], estado, activa, conteos)


async def activar_version(session: AsyncSession, paquete_version: str) -> int:
    """Deja `paquete_version` como la única versión activa. Devuelve su version_id."""
    async with session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CANDADO_IMPORTACION})
        version_id = (await session.execute(
            select(VersionAnalisis.version_id).where(VersionAnalisis.paquete_version == paquete_version)
        )).scalar_one_or_none()
        if version_id is None:
            raise ErrorPaquete(f"la versión {paquete_version} no está importada")
        await _activar(session, version_id)
    return version_id


async def listar_versiones(session: AsyncSession) -> list[dict]:
    filas = await session.execute(
        select(VersionAnalisis.version_id, VersionAnalisis.paquete_version, VersionAnalisis.activa,
               VersionAnalisis.importado_en, func.left(VersionAnalisis.manifest_sha256, 12).label("manifest"))
        .order_by(VersionAnalisis.version_id))
    return [dict(r._mapping) for r in filas]
