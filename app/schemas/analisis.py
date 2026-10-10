"""Respuestas de /api/v1/analisis (resultados importados del paquete versionado)."""

from pydantic import BaseModel, Field


class Atribucion(BaseModel):
    licencia: str
    licencia_url: str | None
    aviso: str
    fuentes: str
    paquete_version: str


class VersionResumen(BaseModel):
    paquete_version: str
    dataset_version: str
    modelo_version: str
    mcda_version: str
    fecha_paquete: str
    descripcion: str
    licencia_datos: str
    activa: bool
    importado_en: str
    manifest_sha256: str


class ListaVersiones(BaseModel):
    activa: str | None
    versiones: list[VersionResumen]


class McdaSitio(BaseModel):
    puntaje: float
    rango: int
    en_top_k: bool


class CriterioValor(BaseModel):
    bruto: float
    norm: float


class SitioResumen(BaseModel):
    sitio_id: str
    lon: float
    lat: float
    ubigeo: str
    distrito: str
    clase_oportunidad: str
    rol: str
    estado_tamizaje: str
    codigo_exclusion: str | None
    dist_punto_existente_m: float | None = Field(
        None, description="Distancia (m) al punto de reciclaje existente más cercano, según el tamizaje del paquete.")
    punto_existente_mas_cercano: str | None = Field(
        None, description="sitio_id del punto existente más cercano (del tamizaje del paquete).")
    propension_percentil: float = Field(
        description="Percentil de la propensión fuera de fold. Contexto: no es probabilidad de éxito ni idoneidad "
                    "y no entra en el puntaje MCDA.")
    mcda: McdaSitio | None = Field(description="Puntaje y rango del esquema pedido; solo sitios apto_tamizaje.")
    mc_frecuencia_top_k: float | None = Field(
        description="Fracción de réplicas Monte Carlo de pesos en que el sitio queda en el top-k.")
    criterios_mcda: dict[str, CriterioValor] | None = Field(
        None, description="K1–K4 bruto y normalizado (mismas claves que el detalle); solo sitios apto_tamizaje.")


class PaginaSitios(BaseModel):
    paquete_version: str
    esquema: str
    total: int
    limit: int
    offset: int
    items: list[SitioResumen]
    avisos: dict[str, str]
    atribucion: Atribucion
