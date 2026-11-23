"""add analisis schema for the versioned results package (v1)

Crea el esquema `analisis` con las tablas que guardan, tal cual, el paquete de
resultados versionado: versión, distritos, sitios, variables, tamizaje,
propensión, criterios y pesos MCDA, MCDA por esquema, Monte Carlo,
concordancia, fuentes y ficha del modelo.

Migración aditiva: no toca ninguna tabla existente (`districts` sigue como
POLYGON; los límites MULTIPOLYGON del paquete van en `analisis.distrito`).

Revision ID: a7c3e9f1b2d4
Revises: 775ff9d49db4
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, None] = '775ff9d49db4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analisis"
DOUBLE = sa.Float(precision=53)


def _en(columna, valores):
    return f"{columna} IN ({', '.join(repr(v) for v in valores)})"


def _entre_0_y_1(columna):
    return f"{columna} >= 0 AND {columna} <= 1"


def _version_fk():
    return sa.ForeignKey(f"{SCHEMA}.version_analisis.version_id", ondelete="CASCADE")


def _fk_sitio(nombre, columnas=("version_id", "sitio_id"), tabla="sitio", ondelete="CASCADE"):
    return sa.ForeignKeyConstraint(
        list(columnas),
        [f"{SCHEMA}.{tabla}.version_id", f"{SCHEMA}.{tabla}.sitio_id"],
        name=nombre,
        ondelete=ondelete,
    )


REGLAS_TAMIZAJE = (
    "t01_territorio", "t02_acceso_osm", "t03_trazabilidad_documental",
    "t04_datos_secundarios", "t05_sin_punto_existente_30m",
)
CRITERIOS_CAMPO = ("c02", "c03", "c04", "c05", "c06", "c07", "c08", "c09")
RESULTADOS_REGLA = ("cumple", "cumple_parcial", "no_cumple")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "version_analisis",
        sa.Column("version_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("paquete_version", sa.String(20), nullable=False),
        sa.Column("dataset_version", sa.String(20), nullable=False),
        sa.Column("dataset_sha256", postgresql.CHAR(64), nullable=False),
        sa.Column("modelo_version", sa.String(40), nullable=False),
        sa.Column("mcda_version", sa.String(20), nullable=False),
        sa.Column("fecha_paquete", sa.DateTime(timezone=True), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", postgresql.CHAR(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("licencia_datos", sa.String(100), nullable=False, server_default="ODbL-1.0"),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("importado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("importado_por", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint("paquete_version"),
        sa.CheckConstraint("char_length(dataset_sha256) = 64", name="ck_version_analisis_dataset_sha256"),
        sa.CheckConstraint("char_length(manifest_sha256) = 64", name="ck_version_analisis_manifest_sha256"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_version_analisis_activa",
        "version_analisis",
        ["activa"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("activa"),
    )

    op.create_table(
        "distrito",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("ubigeo", postgresql.CHAR(6), nullable=False),
        sa.Column("nombre", sa.String(100), nullable=False),
        sa.Column("geometry", Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "ubigeo"),
        sa.CheckConstraint("ubigeo ~ '^1501[0-9]{2}$'", name="ck_distrito_ubigeo"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_analisis_distrito_geometry", "distrito", ["geometry"], schema=SCHEMA, postgresql_using="gist"
    )

    op.create_table(
        "sitio",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        sa.Column("lon", DOUBLE, nullable=False),
        sa.Column("lat", DOUBLE, nullable=False),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("ubigeo", postgresql.CHAR(6), nullable=False),
        sa.Column("distrito", sa.String(100), nullable=False),
        sa.Column("clase_oportunidad", sa.String(40), nullable=False),
        sa.Column("rol", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "sitio_id"),
        sa.ForeignKeyConstraint(
            ["version_id", "ubigeo"],
            [f"{SCHEMA}.distrito.version_id", f"{SCHEMA}.distrito.ubigeo"],
            name="fk_sitio_distrito",
            # Diferida: borrar una versión en cascada elimina ambos extremos en la
            # misma transacción; se comprueba al confirmar.
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("lon >= -77.20 AND lon <= -76.60", name="ck_sitio_lon"),
        sa.CheckConstraint("lat >= -12.55 AND lat <= -11.55", name="ck_sitio_lat"),
        sa.CheckConstraint(
            _en("clase_oportunidad", (
                "paradero_transporte_publico", "mercado_municipal_no_confirmado", "centro_comercial", "parque",
            )),
            name="ck_sitio_clase_oportunidad",
        ),
        sa.CheckConstraint(_en("rol", ("positivo", "fondo")), name="ck_sitio_rol"),
        schema=SCHEMA,
    )
    op.create_index("ix_analisis_sitio_geom", "sitio", ["geom"], schema=SCHEMA, postgresql_using="gist")
    op.create_index("ix_analisis_sitio_version_ubigeo", "sitio", ["version_id", "ubigeo"], schema=SCHEMA)

    op.create_table(
        "sitio_feature",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        *(
            sa.Column(c, DOUBLE, nullable=False)
            for c in (
                "pob_wp_r250", "pob_wp_r500", "pob_wp_r1000",
                "vias_km_km2_r250", "vias_km_km2_r500", "vias_km_km2_r1000",
                "poi_n_km2_r250", "poi_n_km2_r500", "poi_n_km2_r1000",
                "ghsl_frac_construida_r250", "ghsl_frac_construida_r500", "ghsl_frac_construida_r1000",
                "pendiente_grados", "sigersol_gpc_municipal_kg_hab_dia",
            )
        ),
        sa.PrimaryKeyConstraint("version_id", "sitio_id"),
        _fk_sitio("fk_sitio_feature_sitio"),
        sa.CheckConstraint(_entre_0_y_1("ghsl_frac_construida_r250"), name="ck_sitio_feature_ghsl_r250"),
        sa.CheckConstraint(_entre_0_y_1("ghsl_frac_construida_r500"), name="ck_sitio_feature_ghsl_r500"),
        sa.CheckConstraint(_entre_0_y_1("ghsl_frac_construida_r1000"), name="ck_sitio_feature_ghsl_r1000"),
        schema=SCHEMA,
    )

    op.create_table(
        "tamizaje",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        sa.Column("t01_territorio", sa.String(20), nullable=True),
        sa.Column("t02_acceso_osm", sa.String(20), nullable=True),
        sa.Column("t03_trazabilidad_documental", sa.String(20), nullable=True),
        sa.Column("t04_datos_secundarios", sa.String(20), nullable=True),
        sa.Column("dist_punto_existente_m", DOUBLE, nullable=True),
        sa.Column("punto_existente_mas_cercano", sa.String(40), nullable=True),
        sa.Column("t05_sin_punto_existente_30m", sa.String(20), nullable=True),
        sa.Column("codigo_exclusion", sa.String(10), nullable=True),
        sa.Column("motivo_exclusion", sa.Text(), nullable=True),
        sa.Column("estado_tamizaje", sa.String(20), nullable=False),
        *(sa.Column(c, sa.String(20), nullable=True) for c in CRITERIOS_CAMPO),
        sa.Column("estado_verificacion_campo", sa.String(20), nullable=True),
        sa.PrimaryKeyConstraint("version_id", "sitio_id"),
        _fk_sitio("fk_tamizaje_sitio"),
        sa.ForeignKeyConstraint(
            ["version_id", "punto_existente_mas_cercano"],
            [f"{SCHEMA}.sitio.version_id", f"{SCHEMA}.sitio.sitio_id"],
            name="fk_tamizaje_punto_existente",
            # Diferida: borrar una versión en cascada elimina ambos extremos en la
            # misma transacción; se comprueba al confirmar.
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            _en("estado_tamizaje", ("apto_tamizaje", "excluido_tamizaje", "punto_existente")),
            name="ck_tamizaje_estado",
        ),
        *(sa.CheckConstraint(_en(r, RESULTADOS_REGLA), name=f"ck_tamizaje_{r[:3]}") for r in REGLAS_TAMIZAJE),
        *(sa.CheckConstraint(_en(c, ("pendiente_campo",)), name=f"ck_tamizaje_{c}") for c in CRITERIOS_CAMPO),
        sa.CheckConstraint(
            _en("estado_verificacion_campo", ("pendiente_campo",)), name="ck_tamizaje_verificacion_campo"
        ),
        sa.CheckConstraint("dist_punto_existente_m >= 0", name="ck_tamizaje_dist"),
        sa.CheckConstraint(
            "(estado_tamizaje = 'excluido_tamizaje') = (codigo_exclusion IS NOT NULL AND motivo_exclusion IS NOT NULL)",
            name="ck_tamizaje_exclusion",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_analisis_tamizaje_version_estado", "tamizaje", ["version_id", "estado_tamizaje"], schema=SCHEMA
    )

    op.create_table(
        "propension",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        *(
            sa.Column(c, DOUBLE, nullable=False)
            for c in (
                "propension_oof", "propension_oof_de", "percentil_oof", "propension_final", "percentil_final",
                "contrib_clase", "contrib_poblacion", "contrib_vias", "contrib_pois",
                "contrib_construido", "contrib_pendiente", "contrib_sigersol",
            )
        ),
        sa.PrimaryKeyConstraint("version_id", "sitio_id"),
        _fk_sitio("fk_propension_sitio"),
        sa.CheckConstraint("propension_oof > 0 AND propension_oof < 1", name="ck_propension_oof"),
        sa.CheckConstraint("propension_oof_de >= 0", name="ck_propension_oof_de"),
        sa.CheckConstraint("percentil_oof > 0 AND percentil_oof <= 1", name="ck_propension_percentil_oof"),
        sa.CheckConstraint("propension_final > 0 AND propension_final < 1", name="ck_propension_final"),
        sa.CheckConstraint("percentil_final > 0 AND percentil_final <= 1", name="ck_propension_percentil_final"),
        schema=SCHEMA,
    )

    op.create_table(
        "criterio_mcda",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        *(
            sa.Column(c, DOUBLE, nullable=False)
            for c in (
                "k1_demanda_bruto", "k1_demanda_norm", "k2_generacion_bruto", "k2_generacion_norm",
                "k3_brecha_bruto", "k3_brecha_norm", "k4_accesibilidad_bruto", "k4_accesibilidad_norm",
                "contexto_propension_oof",
            )
        ),
        sa.PrimaryKeyConstraint("version_id", "sitio_id"),
        _fk_sitio("fk_criterio_mcda_sitio"),
        sa.CheckConstraint(_entre_0_y_1("k1_demanda_norm"), name="ck_criterio_mcda_k1_norm"),
        sa.CheckConstraint(_entre_0_y_1("k2_generacion_norm"), name="ck_criterio_mcda_k2_norm"),
        sa.CheckConstraint(_entre_0_y_1("k3_brecha_norm"), name="ck_criterio_mcda_k3_norm"),
        sa.CheckConstraint(_entre_0_y_1("k4_accesibilidad_norm"), name="ck_criterio_mcda_k4_norm"),
        sa.CheckConstraint(
            "contexto_propension_oof > 0 AND contexto_propension_oof < 1", name="ck_criterio_mcda_contexto"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "peso_mcda",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("esquema", sa.String(20), nullable=False),
        sa.Column("k1_demanda", DOUBLE, nullable=False),
        sa.Column("k2_generacion", DOUBLE, nullable=False),
        sa.Column("k3_brecha", DOUBLE, nullable=False),
        sa.Column("k4_accesibilidad", DOUBLE, nullable=False),
        sa.PrimaryKeyConstraint("version_id", "esquema"),
        sa.CheckConstraint(_en("esquema", ("iguales", "critic", "entropia")), name="ck_peso_mcda_esquema"),
        sa.CheckConstraint(_entre_0_y_1("k1_demanda"), name="ck_peso_mcda_k1"),
        sa.CheckConstraint(_entre_0_y_1("k2_generacion"), name="ck_peso_mcda_k2"),
        sa.CheckConstraint(_entre_0_y_1("k3_brecha"), name="ck_peso_mcda_k3"),
        sa.CheckConstraint(_entre_0_y_1("k4_accesibilidad"), name="ck_peso_mcda_k4"),
        sa.CheckConstraint(
            "abs(k1_demanda + k2_generacion + k3_brecha + k4_accesibilidad - 1) < 1e-9",
            name="ck_peso_mcda_suma",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "mcda",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("esquema", sa.String(20), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        sa.Column("mcda_version", sa.String(20), nullable=False),
        sa.Column("puntaje", DOUBLE, nullable=False),
        sa.Column("rango", sa.Integer(), nullable=False),
        sa.Column("en_top_k", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "esquema", "sitio_id"),
        _fk_sitio("fk_mcda_criterio", tabla="criterio_mcda"),
        sa.ForeignKeyConstraint(
            ["version_id", "esquema"],
            [f"{SCHEMA}.peso_mcda.version_id", f"{SCHEMA}.peso_mcda.esquema"],
            name="fk_mcda_peso",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("version_id", "esquema", "rango", name="uq_mcda_version_esquema_rango"),
        sa.CheckConstraint(_entre_0_y_1("puntaje"), name="ck_mcda_puntaje"),
        sa.CheckConstraint("rango >= 1", name="ck_mcda_rango"),
        schema=SCHEMA,
    )

    op.create_table(
        "mcda_montecarlo",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("sitio_id", sa.String(40), nullable=False),
        sa.Column("mcda_version", sa.String(20), nullable=False),
        sa.Column("mc_rango_mediana", DOUBLE, nullable=False),
        sa.Column("mc_rango_p05", DOUBLE, nullable=False),
        sa.Column("mc_rango_p95", DOUBLE, nullable=False),
        sa.Column("mc_frecuencia_top_k", DOUBLE, nullable=False),
        sa.PrimaryKeyConstraint("version_id", "sitio_id"),
        _fk_sitio("fk_mcda_montecarlo_criterio", tabla="criterio_mcda"),
        sa.CheckConstraint(
            "mc_rango_p05 >= 1 AND mc_rango_p05 <= mc_rango_mediana AND mc_rango_mediana <= mc_rango_p95",
            name="ck_mcda_montecarlo_rangos",
        ),
        sa.CheckConstraint(_entre_0_y_1("mc_frecuencia_top_k"), name="ck_mcda_montecarlo_frecuencia"),
        schema=SCHEMA,
    )

    op.create_table(
        "concordancia_mcda",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("comparacion", sa.String(100), nullable=False),
        sa.Column("spearman", DOUBLE, nullable=False),
        sa.Column("kendall_tau_b", DOUBLE, nullable=False),
        sa.Column("solapamiento_top110", DOUBLE, nullable=False),
        sa.PrimaryKeyConstraint("version_id", "comparacion"),
        sa.CheckConstraint("spearman >= -1 AND spearman <= 1", name="ck_concordancia_mcda_spearman"),
        sa.CheckConstraint("kendall_tau_b >= -1 AND kendall_tau_b <= 1", name="ck_concordancia_mcda_kendall"),
        sa.CheckConstraint(_entre_0_y_1("solapamiento_top110"), name="ck_concordancia_mcda_solapamiento"),
        schema=SCHEMA,
    )

    op.create_table(
        "fuente",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("codigo", sa.String(50), nullable=False),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("institucion", sa.Text(), nullable=False),
        sa.Column("url_descarga", sa.Text(), nullable=True),
        sa.Column("url_pagina", sa.Text(), nullable=True),
        sa.Column("licencia", sa.Text(), nullable=False),
        sa.Column("licencia_url", sa.Text(), nullable=True),
        sa.Column("licencia_estado", sa.String(30), nullable=False),
        sa.Column("licencia_verificada_en", sa.Date(), nullable=False),
        sa.Column("atribucion", sa.Text(), nullable=False),
        sa.Column("fecha_corte", sa.Text(), nullable=False),
        sa.Column("sha256", postgresql.CHAR(64), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("uso_en_v1", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "codigo"),
        sa.CheckConstraint(_en("tipo", ("primaria", "derivada")), name="ck_fuente_tipo"),
        # `no_verificada` no se admite: un paquete con una fuente sin revisar no se importa.
        sa.CheckConstraint(
            _en("licencia_estado", ("verificada", "sin_licencia_explicita")), name="ck_fuente_licencia_estado"
        ),
        sa.CheckConstraint(
            "licencia_estado <> 'verificada' OR licencia_url IS NOT NULL", name="ck_fuente_licencia_url"
        ),
        sa.CheckConstraint("char_length(sha256) = 64", name="ck_fuente_sha256"),
        sa.CheckConstraint("bytes >= 0", name="ck_fuente_bytes"),
        schema=SCHEMA,
    )

    op.create_table(
        "modelo",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("modelo_version", sa.String(40), nullable=False),
        sa.Column("algoritmo", sa.Text(), nullable=False),
        sa.Column("model_card", postgresql.JSONB(), nullable=False),
        sa.Column("parametros", postgresql.JSONB(), nullable=False),
        sa.Column("metricas_resumen", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("version_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "metrica_semilla",
        sa.Column("version_id", sa.Integer(), _version_fk(), nullable=False),
        sa.Column("semilla", sa.SmallInteger(), nullable=False),
        *(
            sa.Column(c, DOUBLE, nullable=False)
            for c in ("roc_auc", "pr_auc", "boyce", "prevalencia", "recall_top5", "recall_top10", "recall_top20")
        ),
        sa.PrimaryKeyConstraint("version_id", "semilla"),
        sa.CheckConstraint("semilla >= 0", name="ck_metrica_semilla_semilla"),
        sa.CheckConstraint(_entre_0_y_1("roc_auc"), name="ck_metrica_semilla_roc_auc"),
        sa.CheckConstraint(_entre_0_y_1("pr_auc"), name="ck_metrica_semilla_pr_auc"),
        sa.CheckConstraint("boyce >= -1 AND boyce <= 1", name="ck_metrica_semilla_boyce"),
        sa.CheckConstraint(_entre_0_y_1("prevalencia"), name="ck_metrica_semilla_prevalencia"),
        sa.CheckConstraint(_entre_0_y_1("recall_top5"), name="ck_metrica_semilla_recall_top5"),
        sa.CheckConstraint(_entre_0_y_1("recall_top10"), name="ck_metrica_semilla_recall_top10"),
        sa.CheckConstraint(_entre_0_y_1("recall_top20"), name="ck_metrica_semilla_recall_top20"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Borra solo lo creado en upgrade(). Sin CASCADE: si alguien agregó otros
    # objetos al esquema, el DROP SCHEMA falla en vez de borrarlos.
    for tabla in (
        "metrica_semilla", "modelo", "fuente", "concordancia_mcda", "mcda_montecarlo", "mcda",
        "peso_mcda", "criterio_mcda", "propension", "tamizaje", "sitio_feature", "sitio",
        "distrito", "version_analisis",
    ):
        op.drop_table(tabla, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
