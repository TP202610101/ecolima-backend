import json

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.district import District
from app.models.recycling_point import RecyclingPoint


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
