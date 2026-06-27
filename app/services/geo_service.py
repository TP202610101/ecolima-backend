import json

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_zone import CandidateZone
from app.models.district import District
from app.models.recycling_point import RecyclingPoint

_HEATMAP_METRICS: dict[str, str] = {
    "density": "population_density",
    "priority": "ml_score",
    "gap": "dist_to_nearest_point_m",
}

_MATERIAL_KEYWORDS: dict[str, list[str]] = {
    "plastic":    ["plástico", "plastico", "plastic"],
    "paper":      ["papel", "paper", "cartón", "carton", "cardboard"],
    "glass":      ["vidrio", "glass", "cristal"],
    "metal":      ["metal", "aluminio", "aluminum", "lata"],
    "organic":    ["orgánico", "organico", "organic"],
    "electronic": ["electrónico", "electronico", "electronic", "eee", "raee"],
}


def _parse_geom(geom_json: str | None) -> dict | None:
    return json.loads(geom_json) if geom_json else None


async def get_points_geojson(
    db: AsyncSession,
    district_id: int | None = None,
    material: str | None = None,
    verified_only: bool = False,
) -> dict:
    query = select(
        RecyclingPoint.point_id,
        RecyclingPoint.point_type,
        RecyclingPoint.address,
        RecyclingPoint.operator,
        RecyclingPoint.materials_accepted,
        RecyclingPoint.verified,
        RecyclingPoint.source,
        RecyclingPoint.district_id,
        func.ST_AsGeoJSON(RecyclingPoint.geometry).label("geometry_json"),
    )
    if district_id is not None:
        query = query.where(RecyclingPoint.district_id == district_id)
    if material:
        query = query.where(RecyclingPoint.materials_accepted.ilike(f"%{material}%"))
    if verified_only:
        query = query.where(RecyclingPoint.verified.is_(True))

    rows = (await db.execute(query)).mappings().all()
    features = [
        {
            "type": "Feature",
            "geometry": _parse_geom(row["geometry_json"]),
            "properties": {
                "point_id": row["point_id"],
                "point_type": row["point_type"],
                "address": row["address"],
                "operator": row["operator"],
                "materials_accepted": row["materials_accepted"],
                "verified": row["verified"],
                "source": row["source"],
                "district_id": row["district_id"],
            },
        }
        for row in rows
    ]
    return {"type": "FeatureCollection", "features": features}


async def get_point_by_id_geojson(db: AsyncSession, point_id: int) -> dict:
    query = (
        select(
            RecyclingPoint.point_id,
            RecyclingPoint.point_type,
            RecyclingPoint.address,
            RecyclingPoint.operator,
            RecyclingPoint.materials_accepted,
            RecyclingPoint.verified,
            RecyclingPoint.source,
            RecyclingPoint.district_id,
            District.district_name,
            func.ST_AsGeoJSON(RecyclingPoint.geometry).label("geometry_json"),
        )
        .join(District, RecyclingPoint.district_id == District.district_id, isouter=True)
        .where(RecyclingPoint.point_id == point_id)
    )
    row = (await db.execute(query)).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Punto no encontrado.")

    return {
        "type": "Feature",
        "geometry": _parse_geom(row["geometry_json"]),
        "properties": {
            "point_id": row["point_id"],
            "point_type": row["point_type"],
            "address": row["address"],
            "operator": row["operator"],
            "materials_accepted": row["materials_accepted"],
            "verified": row["verified"],
            "source": row["source"],
            "district_id": row["district_id"],
            "district_name": row["district_name"],
        },
    }


async def get_districts_geojson(db: AsyncSession) -> dict:
    query = select(
        District.district_id,
        District.district_name,
        District.area_km2,
        func.ST_AsGeoJSON(District.geometry).label("geometry_json"),
    )
    rows = (await db.execute(query)).mappings().all()
    features = [
        {
            "type": "Feature",
            "geometry": _parse_geom(row["geometry_json"]),
            "properties": {
                "district_id": row["district_id"],
                "district_name": row["district_name"],
                "area_km2": row["area_km2"],
            },
        }
        for row in rows
    ]
    return {"type": "FeatureCollection", "features": features}


def _parse_materials_breakdown(all_materials: str) -> dict:
    if not all_materials:
        return {}
    entries = [e.strip().lower() for e in all_materials.split("|||") if e.strip()]
    return {
        key: cnt
        for key, terms in _MATERIAL_KEYWORDS.items()
        if (cnt := sum(1 for e in entries if any(t in e for t in terms))) > 0
    }


async def get_filtered_points_geojson(
    db: AsyncSession,
    district_id: int | None = None,
    material: str | None = None,
    point_type: str | None = None,
    verified: bool | None = None,
) -> dict:
    query = select(
        RecyclingPoint.point_id,
        RecyclingPoint.point_type,
        RecyclingPoint.address,
        RecyclingPoint.operator,
        RecyclingPoint.materials_accepted,
        RecyclingPoint.verified,
        RecyclingPoint.source,
        RecyclingPoint.district_id,
        func.ST_AsGeoJSON(RecyclingPoint.geometry).label("geometry_json"),
    )
    if district_id is not None:
        query = query.where(RecyclingPoint.district_id == district_id)
    if material:
        query = query.where(RecyclingPoint.materials_accepted.ilike(f"%{material}%"))
    if point_type:
        query = query.where(RecyclingPoint.point_type.ilike(f"%{point_type}%"))
    if verified is not None:
        query = query.where(RecyclingPoint.verified.is_(verified))

    rows = (await db.execute(query)).mappings().all()
    features = [
        {
            "type": "Feature",
            "geometry": _parse_geom(row["geometry_json"]),
            "properties": {
                "point_id": row["point_id"],
                "point_type": row["point_type"],
                "address": row["address"],
                "operator": row["operator"],
                "materials_accepted": row["materials_accepted"],
                "verified": row["verified"],
                "source": row["source"],
                "district_id": row["district_id"],
            },
        }
        for row in rows
    ]
    return {"type": "FeatureCollection", "features": features}


async def get_district_stats(db: AsyncSession, district_id: int) -> dict:
    dist_result = await db.execute(
        select(District.district_name).where(District.district_id == district_id)
    )
    dist_row = dist_result.first()
    if dist_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Distrito no encontrado.")

    stmt = text("""
        WITH point_stats AS (
            SELECT
                COUNT(*)::int                                       AS total_points,
                COUNT(*) FILTER (WHERE verified = true)::int        AS verified_points,
                STRING_AGG(materials_accepted, '|||')               AS all_materials
            FROM recycling_points
            WHERE district_id = :district_id
        ),
        zone_stats AS (
            SELECT
                COUNT(*) FILTER (WHERE is_suitable = 1)::float      AS suitable_count,
                COUNT(*) FILTER (WHERE is_suitable IS NOT NULL)::float AS labeled_count,
                AVG(dist_to_nearest_point_m)                        AS avg_distance_to_nearest_m
            FROM candidate_zones
            WHERE district_id = :district_id
        )
        SELECT
            ps.total_points,
            ps.verified_points,
            ps.all_materials,
            CASE WHEN zs.labeled_count > 0
                 THEN ROUND((zs.suitable_count / zs.labeled_count * 100)::numeric, 1)
                 ELSE NULL
            END AS coverage_pct,
            CASE WHEN zs.avg_distance_to_nearest_m IS NOT NULL
                 THEN ROUND(zs.avg_distance_to_nearest_m::numeric, 1)
                 ELSE NULL
            END AS avg_distance_to_nearest_m
        FROM point_stats ps CROSS JOIN zone_stats zs
    """)
    row = (await db.execute(stmt, {"district_id": district_id})).mappings().first()

    return {
        "district_id": district_id,
        "district_name": dist_row[0],
        "total_points": row["total_points"] or 0,
        "verified_points": row["verified_points"] or 0,
        "materials_breakdown": _parse_materials_breakdown(row["all_materials"] or ""),
        "coverage_pct": float(row["coverage_pct"]) if row["coverage_pct"] is not None else None,
        "avg_distance_to_nearest_m": (
            float(row["avg_distance_to_nearest_m"])
            if row["avg_distance_to_nearest_m"] is not None
            else None
        ),
    }


async def get_comparison_geojson(db: AsyncSession) -> dict:
    current = await get_points_geojson(db)

    query = select(
        CandidateZone.zone_id,
        CandidateZone.centroid_lat,
        CandidateZone.centroid_lon,
        CandidateZone.priority_label,
        CandidateZone.recommendation_reason,
        CandidateZone.coverage_gap_m,
        CandidateZone.district_id,
        func.ST_AsGeoJSON(CandidateZone.geometry).label("geometry_json"),
    ).where(CandidateZone.is_recommended.is_(True))

    rows = (await db.execute(query)).mappings().all()
    recommended_features = [
        {
            "type": "Feature",
            "geometry": _parse_geom(row["geometry_json"]),
            "properties": {
                "zone_id": row["zone_id"],
                "centroid_lat": row["centroid_lat"],
                "centroid_lon": row["centroid_lon"],
                "priority_label": row["priority_label"],
                "recommendation_reason": row["recommendation_reason"],
                "coverage_gap_m": row["coverage_gap_m"],
                "district_id": row["district_id"],
            },
        }
        for row in rows
    ]
    return {
        "current": current,
        "recommended": {"type": "FeatureCollection", "features": recommended_features},
    }


async def get_heatmap_geojson(
    db: AsyncSession,
    district_id: int | None = None,
    metric: str = "density",
) -> dict:
    if metric not in _HEATMAP_METRICS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"metric debe ser uno de: {list(_HEATMAP_METRICS.keys())}",
        )

    metric_col = getattr(CandidateZone, _HEATMAP_METRICS[metric])
    query = select(
        CandidateZone.zone_id,
        CandidateZone.district_id,
        CandidateZone.centroid_lat,
        CandidateZone.centroid_lon,
        metric_col.label("value"),
    ).where(metric_col.isnot(None))

    if district_id is not None:
        query = query.where(CandidateZone.district_id == district_id)

    rows = (await db.execute(query)).mappings().all()
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row["centroid_lon"], row["centroid_lat"]],
            },
            "properties": {
                "zone_id": row["zone_id"],
                "district_id": row["district_id"],
                "value": float(row["value"]),
                "metric": metric,
            },
        }
        for row in rows
    ]
    return {"type": "FeatureCollection", "features": features}


async def get_nearby_points_geojson(
    db: AsyncSession, lat: float, lon: float, radius_m: int
) -> dict:
    stmt = text("""
        SELECT
            rp.point_id,
            rp.point_type,
            rp.address,
            rp.operator,
            rp.materials_accepted,
            rp.verified,
            rp.source,
            rp.district_id,
            ST_AsGeoJSON(rp.geometry)  AS geometry_json,
            ST_Distance(
                rp.geometry::geography,
                ST_SetSRID(ST_Point(:lon, :lat), 4326)::geography
            ) AS distance_m
        FROM recycling_points rp
        WHERE ST_DWithin(
            rp.geometry::geography,
            ST_SetSRID(ST_Point(:lon, :lat), 4326)::geography,
            :radius_m
        )
        ORDER BY distance_m ASC
    """)
    rows = (
        await db.execute(stmt, {"lat": lat, "lon": lon, "radius_m": radius_m})
    ).mappings().all()

    features = [
        {
            "type": "Feature",
            "geometry": _parse_geom(row["geometry_json"]),
            "properties": {
                "point_id": row["point_id"],
                "point_type": row["point_type"],
                "address": row["address"],
                "operator": row["operator"],
                "materials_accepted": row["materials_accepted"],
                "verified": row["verified"],
                "source": row["source"],
                "district_id": row["district_id"],
                "distance_m": float(row["distance_m"]),
            },
        }
        for row in rows
    ]
    return {"type": "FeatureCollection", "features": features}


# ── Pipeline ML — queries PostGIS ─────────────────────────────────────────────

async def calculate_is_suitable(db: AsyncSession, threshold_m: int = 200) -> dict:
    """
    Etiqueta candidate_zones con is_suitable = 1/0 según si hay un recycling_point
    a ≤ threshold_m metros. Solo actualiza zonas en distritos con datos reales;
    el resto queda NULL y no entra al training set.
    ::geography garantiza que el umbral sea en metros, no en grados.
    """
    await db.execute(
        text("""
            UPDATE candidate_zones cz
            SET is_suitable = CASE
                WHEN EXISTS (
                    SELECT 1 FROM recycling_points rp
                    WHERE ST_DWithin(
                        rp.geometry::geography,
                        cz.geometry::geography,
                        :threshold_m
                    )
                ) THEN 1
                ELSE 0
            END
            WHERE cz.district_id IN (
                SELECT DISTINCT district_id FROM recycling_points
            )
        """),
        {"threshold_m": threshold_m},
    )
    await db.commit()

    stats = (
        await db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE is_suitable IS NOT NULL) AS updated_zones,
                    COUNT(*) FILTER (WHERE is_suitable = 1)         AS positive_labels,
                    COUNT(*) FILTER (WHERE is_suitable = 0)         AS negative_labels,
                    COUNT(*) FILTER (WHERE is_suitable IS NULL)     AS null_zones
                FROM candidate_zones
            """)
        )
    ).mappings().first()

    return {
        "updated_zones":   int(stats["updated_zones"]),
        "positive_labels": int(stats["positive_labels"]),
        "negative_labels": int(stats["negative_labels"]),
        "null_zones":      int(stats["null_zones"]),
    }


async def calculate_coverage_gaps(db: AsyncSession) -> dict:
    """
    Actualiza coverage_gap_m (Sección C) y dist_to_nearest_point_m (Sección B)
    con la distancia mínima al recycling_point más cercano para cada celda.
    FROM-subquery calcula la distancia una sola vez por zona.
    ::geography garantiza metros exactos, no grados.
    """
    result = await db.execute(
        text("""
            UPDATE candidate_zones cz
            SET
                coverage_gap_m          = sub.dist_m,
                dist_to_nearest_point_m = sub.dist_m
            FROM (
                SELECT
                    cz2.zone_id,
                    MIN(
                        ST_Distance(rp.geometry::geography, cz2.geometry::geography)
                    ) AS dist_m
                FROM candidate_zones cz2
                CROSS JOIN recycling_points rp
                GROUP BY cz2.zone_id
            ) sub
            WHERE cz.zone_id = sub.zone_id
        """)
    )
    await db.commit()
    return {"updated_zones": result.rowcount if result.rowcount >= 0 else 0}


async def calculate_existing_points_500m(db: AsyncSession) -> dict:
    """
    Cuenta recycling_points en buffer de 500m por celda y actualiza
    existing_points_500m (Sección B). Valor 0 cuando no hay puntos cercanos.
    ::geography garantiza que los 500m sean metros, no grados.
    """
    result = await db.execute(
        text("""
            UPDATE candidate_zones cz
            SET existing_points_500m = (
                SELECT COUNT(*)::int
                FROM recycling_points rp
                WHERE ST_DWithin(
                    rp.geometry::geography,
                    cz.geometry::geography,
                    500
                )
            )
        """)
    )
    await db.commit()
    return {"updated_zones": result.rowcount if result.rowcount >= 0 else 0}


async def get_saturation_data(db: AsyncSession) -> list[dict]:
    """
    Calcula el porcentaje de saturación por distrito.
    already_covered = zonas is_recommended=TRUE con existing_points_500m > 0
    (indica que ya hay al menos un punto real en 500m, independiente de is_suitable)
    saturation_pct = already_covered / total_recommended × 100
    Semáforo: verde 0-50%, amarillo 51-80%, rojo >80%
    """
    rows = (await db.execute(text("""
        SELECT
            d.district_id,
            d.district_name,
            COUNT(cz.zone_id) FILTER (WHERE cz.is_recommended = TRUE)
                AS total_recommended,
            COUNT(cz.zone_id) FILTER (WHERE cz.is_recommended = TRUE AND cz.existing_points_500m > 0)
                AS already_covered,
            CASE
                WHEN COUNT(cz.zone_id) FILTER (WHERE cz.is_recommended = TRUE) = 0 THEN 0.0
                ELSE ROUND(
                    100.0
                    * COUNT(cz.zone_id) FILTER (WHERE cz.is_recommended = TRUE AND cz.existing_points_500m > 0)
                    / COUNT(cz.zone_id) FILTER (WHERE cz.is_recommended = TRUE),
                    2
                )
            END AS saturation_pct
        FROM districts d
        LEFT JOIN candidate_zones cz ON cz.district_id = d.district_id
        GROUP BY d.district_id, d.district_name
        ORDER BY saturation_pct DESC NULLS LAST, d.district_name
    """))).mappings().fetchall()

    result = []
    for row in rows:
        pct = float(row["saturation_pct"])
        if pct <= 50:
            traffic_light = "verde"
        elif pct <= 80:
            traffic_light = "amarillo"
        else:
            traffic_light = "rojo"
        result.append({
            "district_id": row["district_id"],
            "district_name": row["district_name"],
            "total_recommended": int(row["total_recommended"]),
            "already_covered": int(row["already_covered"]),
            "saturation_pct": pct,
            "status": traffic_light,
        })
    return result
