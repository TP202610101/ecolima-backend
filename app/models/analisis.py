"""Tablas del esquema `analisis`: resultados importados del paquete versionado.

El backend no recalcula nada: estas tablas guardan, tal cual, lo que trae el
paquete de resultados (sitios, tamizaje, propensión, criterios y MCDA) junto
con la versión de la que proviene cada fila. Conviven con las tablas del
prototipo (`districts`, `candidate_zones`, ...) sin tocarlas.

Reglas de lectura que las restricciones refuerzan:
- ningún estado de tamizaje es «viable»;
- la propensión no es idoneidad: se muestra su percentil;
- la MCDA ordena oportunidades; la propensión solo es contexto.

Toda fila pertenece a una `version_analisis` y se borra con ella (CASCADE).
Solo una versión puede estar activa a la vez (índice único parcial).
"""

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base

SCHEMA = "analisis"

# Enumeraciones del contrato del paquete v1.
CLASES_OPORTUNIDAD = (
    "paradero_transporte_publico",
    "mercado_municipal_no_confirmado",
    "centro_comercial",
    "parque",
)
ROLES = ("positivo", "fondo")
ESTADOS_TAMIZAJE = ("apto_tamizaje", "excluido_tamizaje", "punto_existente")
RESULTADOS_REGLA = ("cumple", "cumple_parcial", "no_cumple")
ESTADOS_CAMPO = ("pendiente_campo",)
ESQUEMAS_MCDA = ("iguales", "critic", "entropia")
TIPOS_FUENTE = ("primaria", "derivada")
# `no_verificada` no se admite: un paquete con una fuente sin revisar no se importa.
ESTADOS_LICENCIA = ("verificada", "sin_licencia_explicita")

REGLAS_TAMIZAJE = (
    "t01_territorio",
    "t02_acceso_osm",
    "t03_trazabilidad_documental",
    "t04_datos_secundarios",
    "t05_sin_punto_existente_30m",
)
CRITERIOS_CAMPO = ("c02", "c03", "c04", "c05", "c06", "c07", "c08", "c09")
FEATURES = (
    "pob_wp_r250", "pob_wp_r500", "pob_wp_r1000",
    "vias_km_km2_r250", "vias_km_km2_r500", "vias_km_km2_r1000",
    "poi_n_km2_r250", "poi_n_km2_r500", "poi_n_km2_r1000",
    "ghsl_frac_construida_r250", "ghsl_frac_construida_r500", "ghsl_frac_construida_r1000",
    "pendiente_grados",
    "sigersol_gpc_municipal_kg_hab_dia",
)
CONTRIBUCIONES = (
    "contrib_clase", "contrib_poblacion", "contrib_vias", "contrib_pois",
    "contrib_construido", "contrib_pendiente", "contrib_sigersol",
)


def _en(columna: str, valores: tuple[str, ...]) -> str:
    """Expresión SQL `columna IN ('a', 'b', ...)` para un CheckConstraint."""
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{columna} IN ({lista})"


def _entre_0_y_1(columna: str) -> str:
    return f"{columna} >= 0 AND {columna} <= 1"


def _fk_sitio(nombre: str, columnas=("version_id", "sitio_id")) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        list(columnas),
        [f"{SCHEMA}.sitio.version_id", f"{SCHEMA}.sitio.sitio_id"],
        name=nombre,
        ondelete="CASCADE",
    )


def _version_fk() -> ForeignKey:
    return ForeignKey(f"{SCHEMA}.version_analisis.version_id", ondelete="CASCADE")


def _hijos(clase: str, back_populates: str, **kwargs):
    """Relación padre → hijos que se borran con el padre (FK con ON DELETE CASCADE)."""
    return relationship(
        clase, back_populates=back_populates, cascade="all, delete-orphan", passive_deletes=True, **kwargs
    )


class VersionAnalisis(Base):
    """Un paquete importado: versiones de paquete, dataset, modelo y MCDA."""

    __tablename__ = "version_analisis"
    __table_args__ = (
        # Solo una versión activa a la vez.
        Index(
            "uq_version_analisis_activa",
            "activa",
            unique=True,
            postgresql_where=text("activa"),
        ),
        CheckConstraint("char_length(dataset_sha256) = 64", name="ck_version_analisis_dataset_sha256"),
        CheckConstraint("char_length(manifest_sha256) = 64", name="ck_version_analisis_manifest_sha256"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True, autoincrement=True)
    paquete_version = Column(String(20), nullable=False, unique=True)
    dataset_version = Column(String(20), nullable=False)
    dataset_sha256 = Column(CHAR(64), nullable=False)
    modelo_version = Column(String(40), nullable=False)
    mcda_version = Column(String(20), nullable=False)
    fecha_paquete = Column(DateTime(timezone=True), nullable=False)
    descripcion = Column(Text, nullable=False)
    # sha256 del propio manifest.json (el manifiesto no se lista a sí mismo).
    manifest_sha256 = Column(CHAR(64), nullable=False)
    # Manifiesto completo: entorno, conteos, criterios, pesos, historial y archivos.
    manifest = Column(JSONB, nullable=False)
    licencia_datos = Column(String(100), nullable=False, server_default="ODbL-1.0")
    activa = Column(Boolean, nullable=False, server_default=text("false"))
    importado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    importado_por = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)

    distritos = _hijos("DistritoAnalisis", "version")
    sitios = _hijos("Sitio", "version")
    pesos_mcda = _hijos("PesoMcda", "version")
    fuentes = _hijos("Fuente", "version")
    concordancias_mcda = _hijos("ConcordanciaMcda", "version")
    metricas_semilla = _hijos("MetricaSemilla", "version")
    modelo = _hijos("ModeloAnalisis", "version", uselist=False)


class DistritoAnalisis(Base):
    """Límites distritales del paquete (`datos/distritos.geojson`), MULTIPOLYGON."""

    __tablename__ = "distrito"
    __table_args__ = (
        CheckConstraint("ubigeo ~ '^1501[0-9]{2}$'", name="ck_distrito_ubigeo"),
        Index("ix_analisis_distrito_geometry", "geometry", postgresql_using="gist"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, _version_fk(), primary_key=True)
    ubigeo = Column(CHAR(6), primary_key=True)
    nombre = Column(String(100), nullable=False)
    geometry = Column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False)

    version = relationship("VersionAnalisis", back_populates="distritos")
    sitios = relationship("Sitio", back_populates="distrito_ref", viewonly=True)


class Sitio(Base):
    """Un sitio discreto (`salidas/sitios.csv`): fondo o punto existente."""

    __tablename__ = "sitio"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "ubigeo"],
            [f"{SCHEMA}.distrito.version_id", f"{SCHEMA}.distrito.ubigeo"],
            name="fk_sitio_distrito",
            # Diferida: borrar una versión en cascada elimina ambos extremos en la
            # misma transacción; se comprueba al confirmar.
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("lon >= -77.20 AND lon <= -76.60", name="ck_sitio_lon"),
        CheckConstraint("lat >= -12.55 AND lat <= -11.55", name="ck_sitio_lat"),
        CheckConstraint(_en("clase_oportunidad", CLASES_OPORTUNIDAD), name="ck_sitio_clase_oportunidad"),
        CheckConstraint(_en("rol", ROLES), name="ck_sitio_rol"),
        Index("ix_analisis_sitio_geom", "geom", postgresql_using="gist"),
        Index("ix_analisis_sitio_version_ubigeo", "version_id", "ubigeo"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, _version_fk(), primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    # Coordenadas con precisión completa; `geom` se deriva de ellas al importar.
    lon = Column(Float(precision=53), nullable=False)
    lat = Column(Float(precision=53), nullable=False)
    geom = Column(Geometry("POINT", srid=4326, spatial_index=False), nullable=False)
    ubigeo = Column(CHAR(6), nullable=False)
    distrito = Column(String(100), nullable=False)
    clase_oportunidad = Column(String(40), nullable=False)
    rol = Column(String(10), nullable=False)

    version = relationship("VersionAnalisis", back_populates="sitios")
    distrito_ref = relationship("DistritoAnalisis", back_populates="sitios", viewonly=True)
    feature = _hijos("SitioFeature", "sitio", uselist=False)
    tamizaje = relationship(
        "Tamizaje",
        back_populates="sitio",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
        # Hay dos FK de `tamizaje` hacia `sitio`; esta es la del propio sitio.
        primaryjoin="and_(Sitio.version_id == foreign(Tamizaje.version_id), "
        "Sitio.sitio_id == foreign(Tamizaje.sitio_id))",
    )
    propension = _hijos("Propension", "sitio", uselist=False)
    criterio_mcda = _hijos("CriterioMcda", "sitio", uselist=False)


class SitioFeature(Base):
    """Variables numéricas por sitio (`salidas/features.csv`). Ninguna admite nulos.

    La decimoquinta variable, `clase_oportunidad`, ya está en `sitio`.
    """

    __tablename__ = "sitio_feature"
    __table_args__ = (
        _fk_sitio("fk_sitio_feature_sitio"),
        CheckConstraint(_entre_0_y_1("ghsl_frac_construida_r250"), name="ck_sitio_feature_ghsl_r250"),
        CheckConstraint(_entre_0_y_1("ghsl_frac_construida_r500"), name="ck_sitio_feature_ghsl_r500"),
        CheckConstraint(_entre_0_y_1("ghsl_frac_construida_r1000"), name="ck_sitio_feature_ghsl_r1000"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    pob_wp_r250 = Column(Float(precision=53), nullable=False)
    pob_wp_r500 = Column(Float(precision=53), nullable=False)
    pob_wp_r1000 = Column(Float(precision=53), nullable=False)
    vias_km_km2_r250 = Column(Float(precision=53), nullable=False)
    vias_km_km2_r500 = Column(Float(precision=53), nullable=False)
    vias_km_km2_r1000 = Column(Float(precision=53), nullable=False)
    poi_n_km2_r250 = Column(Float(precision=53), nullable=False)
    poi_n_km2_r500 = Column(Float(precision=53), nullable=False)
    poi_n_km2_r1000 = Column(Float(precision=53), nullable=False)
    ghsl_frac_construida_r250 = Column(Float(precision=53), nullable=False)
    ghsl_frac_construida_r500 = Column(Float(precision=53), nullable=False)
    ghsl_frac_construida_r1000 = Column(Float(precision=53), nullable=False)
    pendiente_grados = Column(Float(precision=53), nullable=False)
    sigersol_gpc_municipal_kg_hab_dia = Column(Float(precision=53), nullable=False)

    sitio = relationship("Sitio", back_populates="feature")


class Tamizaje(Base):
    """Tamizaje con datos secundarios (`salidas/tamizaje.csv`). Nunca «viable»."""

    __tablename__ = "tamizaje"
    __table_args__ = (
        _fk_sitio("fk_tamizaje_sitio"),
        # El punto existente más cercano debe ser un sitio de la misma versión.
        ForeignKeyConstraint(
            ["version_id", "punto_existente_mas_cercano"],
            [f"{SCHEMA}.sitio.version_id", f"{SCHEMA}.sitio.sitio_id"],
            name="fk_tamizaje_punto_existente",
            # Diferida: borrar una versión en cascada elimina ambos extremos en la
            # misma transacción; se comprueba al confirmar.
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(_en("estado_tamizaje", ESTADOS_TAMIZAJE), name="ck_tamizaje_estado"),
        *(CheckConstraint(_en(r, RESULTADOS_REGLA), name=f"ck_tamizaje_{r[:3]}") for r in REGLAS_TAMIZAJE),
        *(CheckConstraint(_en(c, ESTADOS_CAMPO), name=f"ck_tamizaje_{c}") for c in CRITERIOS_CAMPO),
        CheckConstraint(_en("estado_verificacion_campo", ESTADOS_CAMPO), name="ck_tamizaje_verificacion_campo"),
        CheckConstraint("dist_punto_existente_m >= 0", name="ck_tamizaje_dist"),
        # Exclusión con código y motivo solo en `excluido_tamizaje`.
        CheckConstraint(
            "(estado_tamizaje = 'excluido_tamizaje') = (codigo_exclusion IS NOT NULL AND motivo_exclusion IS NOT NULL)",
            name="ck_tamizaje_exclusion",
        ),
        Index("ix_analisis_tamizaje_version_estado", "version_id", "estado_tamizaje"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    t01_territorio = Column(String(20), nullable=True)
    t02_acceso_osm = Column(String(20), nullable=True)
    t03_trazabilidad_documental = Column(String(20), nullable=True)
    t04_datos_secundarios = Column(String(20), nullable=True)
    dist_punto_existente_m = Column(Float(precision=53), nullable=True)
    punto_existente_mas_cercano = Column(String(40), nullable=True)
    t05_sin_punto_existente_30m = Column(String(20), nullable=True)
    codigo_exclusion = Column(String(10), nullable=True)
    motivo_exclusion = Column(Text, nullable=True)
    estado_tamizaje = Column(String(20), nullable=False)
    c02 = Column(String(20), nullable=True)
    c03 = Column(String(20), nullable=True)
    c04 = Column(String(20), nullable=True)
    c05 = Column(String(20), nullable=True)
    c06 = Column(String(20), nullable=True)
    c07 = Column(String(20), nullable=True)
    c08 = Column(String(20), nullable=True)
    c09 = Column(String(20), nullable=True)
    estado_verificacion_campo = Column(String(20), nullable=True)

    sitio = relationship(
        "Sitio",
        back_populates="tamizaje",
        primaryjoin="and_(Sitio.version_id == foreign(Tamizaje.version_id), "
        "Sitio.sitio_id == foreign(Tamizaje.sitio_id))",
    )


class Propension(Base):
    """Propensión de emplazamiento (`salidas/propension.csv`). Se muestra el percentil.

    `rol` no se repite aquí: está en `sitio` y el importador verifica que coincida.
    """

    __tablename__ = "propension"
    __table_args__ = (
        _fk_sitio("fk_propension_sitio"),
        CheckConstraint("propension_oof > 0 AND propension_oof < 1", name="ck_propension_oof"),
        CheckConstraint("propension_oof_de >= 0", name="ck_propension_oof_de"),
        CheckConstraint("percentil_oof > 0 AND percentil_oof <= 1", name="ck_propension_percentil_oof"),
        CheckConstraint("propension_final > 0 AND propension_final < 1", name="ck_propension_final"),
        CheckConstraint("percentil_final > 0 AND percentil_final <= 1", name="ck_propension_percentil_final"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    propension_oof = Column(Float(precision=53), nullable=False)
    propension_oof_de = Column(Float(precision=53), nullable=False)
    percentil_oof = Column(Float(precision=53), nullable=False)
    propension_final = Column(Float(precision=53), nullable=False)
    percentil_final = Column(Float(precision=53), nullable=False)
    contrib_clase = Column(Float(precision=53), nullable=False)
    contrib_poblacion = Column(Float(precision=53), nullable=False)
    contrib_vias = Column(Float(precision=53), nullable=False)
    contrib_pois = Column(Float(precision=53), nullable=False)
    contrib_construido = Column(Float(precision=53), nullable=False)
    contrib_pendiente = Column(Float(precision=53), nullable=False)
    contrib_sigersol = Column(Float(precision=53), nullable=False)

    sitio = relationship("Sitio", back_populates="propension")


class CriterioMcda(Base):
    """Criterios K1–K4 (`salidas/criterios_mcda.csv`), solo sitios `apto_tamizaje`.

    Que el sitio sea `apto_tamizaje` lo verifica el importador (validación 8);
    `mcda` y `mcda_montecarlo` cuelgan de esta tabla, así que tampoco pueden
    tener sitios fuera de ella.
    """

    __tablename__ = "criterio_mcda"
    __table_args__ = (
        _fk_sitio("fk_criterio_mcda_sitio"),
        CheckConstraint(_entre_0_y_1("k1_demanda_norm"), name="ck_criterio_mcda_k1_norm"),
        CheckConstraint(_entre_0_y_1("k2_generacion_norm"), name="ck_criterio_mcda_k2_norm"),
        CheckConstraint(_entre_0_y_1("k3_brecha_norm"), name="ck_criterio_mcda_k3_norm"),
        CheckConstraint(_entre_0_y_1("k4_accesibilidad_norm"), name="ck_criterio_mcda_k4_norm"),
        CheckConstraint(
            "contexto_propension_oof > 0 AND contexto_propension_oof < 1",
            name="ck_criterio_mcda_contexto",
        ),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    k1_demanda_bruto = Column(Float(precision=53), nullable=False)
    k1_demanda_norm = Column(Float(precision=53), nullable=False)
    k2_generacion_bruto = Column(Float(precision=53), nullable=False)
    k2_generacion_norm = Column(Float(precision=53), nullable=False)
    k3_brecha_bruto = Column(Float(precision=53), nullable=False)
    k3_brecha_norm = Column(Float(precision=53), nullable=False)
    k4_accesibilidad_bruto = Column(Float(precision=53), nullable=False)
    k4_accesibilidad_norm = Column(Float(precision=53), nullable=False)
    # Solo contexto: no entra en el puntaje.
    contexto_propension_oof = Column(Float(precision=53), nullable=False)

    sitio = relationship("Sitio", back_populates="criterio_mcda")
    mcda = _hijos("Mcda", "criterio")
    montecarlo = _hijos("McdaMontecarlo", "criterio", uselist=False)


class PesoMcda(Base):
    """Pesos por esquema (`salidas/pesos_mcda.csv`). `iguales` es el principal."""

    __tablename__ = "peso_mcda"
    __table_args__ = (
        CheckConstraint(_en("esquema", ESQUEMAS_MCDA), name="ck_peso_mcda_esquema"),
        CheckConstraint(_entre_0_y_1("k1_demanda"), name="ck_peso_mcda_k1"),
        CheckConstraint(_entre_0_y_1("k2_generacion"), name="ck_peso_mcda_k2"),
        CheckConstraint(_entre_0_y_1("k3_brecha"), name="ck_peso_mcda_k3"),
        CheckConstraint(_entre_0_y_1("k4_accesibilidad"), name="ck_peso_mcda_k4"),
        CheckConstraint(
            "abs(k1_demanda + k2_generacion + k3_brecha + k4_accesibilidad - 1) < 1e-9",
            name="ck_peso_mcda_suma",
        ),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, _version_fk(), primary_key=True)
    esquema = Column(String(20), primary_key=True)
    k1_demanda = Column(Float(precision=53), nullable=False)
    k2_generacion = Column(Float(precision=53), nullable=False)
    k3_brecha = Column(Float(precision=53), nullable=False)
    k4_accesibilidad = Column(Float(precision=53), nullable=False)

    version = relationship("VersionAnalisis", back_populates="pesos_mcda")


class Mcda(Base):
    """Puntaje y rango por sitio y esquema (`salidas/mcda.csv`)."""

    __tablename__ = "mcda"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "sitio_id"],
            [f"{SCHEMA}.criterio_mcda.version_id", f"{SCHEMA}.criterio_mcda.sitio_id"],
            name="fk_mcda_criterio",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["version_id", "esquema"],
            [f"{SCHEMA}.peso_mcda.version_id", f"{SCHEMA}.peso_mcda.esquema"],
            name="fk_mcda_peso",
            ondelete="CASCADE",
        ),
        # `rango` es una permutación dentro de cada esquema.
        UniqueConstraint("version_id", "esquema", "rango", name="uq_mcda_version_esquema_rango"),
        CheckConstraint(_entre_0_y_1("puntaje"), name="ck_mcda_puntaje"),
        CheckConstraint("rango >= 1", name="ck_mcda_rango"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True)
    esquema = Column(String(20), primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    # Igual a version_analisis.mcda_version; se conserva por fila como en el paquete.
    mcda_version = Column(String(20), nullable=False)
    puntaje = Column(Float(precision=53), nullable=False)
    rango = Column(Integer, nullable=False)
    en_top_k = Column(Boolean, nullable=False)

    criterio = relationship("CriterioMcda", back_populates="mcda")


class McdaMontecarlo(Base):
    """Monte Carlo de pesos (`salidas/mcda_montecarlo.csv`): una fila por sitio."""

    __tablename__ = "mcda_montecarlo"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "sitio_id"],
            [f"{SCHEMA}.criterio_mcda.version_id", f"{SCHEMA}.criterio_mcda.sitio_id"],
            name="fk_mcda_montecarlo_criterio",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "mc_rango_p05 >= 1 AND mc_rango_p05 <= mc_rango_mediana AND mc_rango_mediana <= mc_rango_p95",
            name="ck_mcda_montecarlo_rangos",
        ),
        CheckConstraint(_entre_0_y_1("mc_frecuencia_top_k"), name="ck_mcda_montecarlo_frecuencia"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, primary_key=True)
    sitio_id = Column(String(40), primary_key=True)
    mcda_version = Column(String(20), nullable=False)
    mc_rango_mediana = Column(Float(precision=53), nullable=False)
    mc_rango_p05 = Column(Float(precision=53), nullable=False)
    mc_rango_p95 = Column(Float(precision=53), nullable=False)
    mc_frecuencia_top_k = Column(Float(precision=53), nullable=False)

    criterio = relationship("CriterioMcda", back_populates="montecarlo")


class ConcordanciaMcda(Base):
    """Concordancia entre esquemas y escenarios (`salidas/concordancia_mcda.csv`). Informativa."""

    __tablename__ = "concordancia_mcda"
    __table_args__ = (
        CheckConstraint("spearman >= -1 AND spearman <= 1", name="ck_concordancia_mcda_spearman"),
        CheckConstraint("kendall_tau_b >= -1 AND kendall_tau_b <= 1", name="ck_concordancia_mcda_kendall"),
        CheckConstraint(_entre_0_y_1("solapamiento_top110"), name="ck_concordancia_mcda_solapamiento"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, _version_fk(), primary_key=True)
    comparacion = Column(String(100), primary_key=True)
    spearman = Column(Float(precision=53), nullable=False)
    kendall_tau_b = Column(Float(precision=53), nullable=False)
    solapamiento_top110 = Column(Float(precision=53), nullable=False)

    version = relationship("VersionAnalisis", back_populates="concordancias_mcda")


class Fuente(Base):
    """Fuentes con licencia y atribución (`datos/fuentes.csv`)."""

    __tablename__ = "fuente"
    __table_args__ = (
        CheckConstraint(_en("tipo", TIPOS_FUENTE), name="ck_fuente_tipo"),
        CheckConstraint(_en("licencia_estado", ESTADOS_LICENCIA), name="ck_fuente_licencia_estado"),
        CheckConstraint(
            "licencia_estado <> 'verificada' OR licencia_url IS NOT NULL",
            name="ck_fuente_licencia_url",
        ),
        CheckConstraint("char_length(sha256) = 64", name="ck_fuente_sha256"),
        CheckConstraint("bytes >= 0", name="ck_fuente_bytes"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, _version_fk(), primary_key=True)
    codigo = Column(String(50), primary_key=True)
    tipo = Column(String(10), nullable=False)
    nombre = Column(Text, nullable=False)
    institucion = Column(Text, nullable=False)
    url_descarga = Column(Text, nullable=True)
    url_pagina = Column(Text, nullable=True)
    licencia = Column(Text, nullable=False)
    licencia_url = Column(Text, nullable=True)
    licencia_estado = Column(String(30), nullable=False)
    licencia_verificada_en = Column(Date, nullable=False)
    atribucion = Column(Text, nullable=False)
    fecha_corte = Column(Text, nullable=False)
    sha256 = Column(CHAR(64), nullable=False)
    bytes = Column(BigInteger, nullable=False)
    uso_en_v1 = Column(Text, nullable=False)

    version = relationship("VersionAnalisis", back_populates="fuentes")


class ModeloAnalisis(Base):
    """Ficha del modelo de propensión: `model_card.json` y parámetros portables.

    No se guarda `logistica.joblib`: el backend no carga pickles.
    """

    __tablename__ = "modelo"
    __table_args__ = ({"schema": SCHEMA},)

    version_id = Column(Integer, _version_fk(), primary_key=True)
    modelo_version = Column(String(40), nullable=False)
    algoritmo = Column(Text, nullable=False)
    model_card = Column(JSONB, nullable=False)
    parametros = Column(JSONB, nullable=False)
    metricas_resumen = Column(JSONB, nullable=False)

    version = relationship("VersionAnalisis", back_populates="modelo")


class MetricaSemilla(Base):
    """Métricas de validación por semilla (`modelo/metricas_por_semilla.csv`)."""

    __tablename__ = "metrica_semilla"
    __table_args__ = (
        CheckConstraint("semilla >= 0", name="ck_metrica_semilla_semilla"),
        CheckConstraint(_entre_0_y_1("roc_auc"), name="ck_metrica_semilla_roc_auc"),
        CheckConstraint(_entre_0_y_1("pr_auc"), name="ck_metrica_semilla_pr_auc"),
        CheckConstraint("boyce >= -1 AND boyce <= 1", name="ck_metrica_semilla_boyce"),
        CheckConstraint(_entre_0_y_1("prevalencia"), name="ck_metrica_semilla_prevalencia"),
        CheckConstraint(_entre_0_y_1("recall_top5"), name="ck_metrica_semilla_recall_top5"),
        CheckConstraint(_entre_0_y_1("recall_top10"), name="ck_metrica_semilla_recall_top10"),
        CheckConstraint(_entre_0_y_1("recall_top20"), name="ck_metrica_semilla_recall_top20"),
        {"schema": SCHEMA},
    )

    version_id = Column(Integer, _version_fk(), primary_key=True)
    semilla = Column(SmallInteger, primary_key=True)
    roc_auc = Column(Float(precision=53), nullable=False)
    pr_auc = Column(Float(precision=53), nullable=False)
    boyce = Column(Float(precision=53), nullable=False)
    prevalencia = Column(Float(precision=53), nullable=False)
    recall_top5 = Column(Float(precision=53), nullable=False)
    recall_top10 = Column(Float(precision=53), nullable=False)
    recall_top20 = Column(Float(precision=53), nullable=False)

    version = relationship("VersionAnalisis", back_populates="metricas_semilla")
