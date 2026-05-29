import os
import asyncio
from dotenv import load_dotenv
import asyncpg

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/ecolima")
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

DISTRICT_POLYGON_WKT = (
    "POLYGON((-77.06 -12.08, -77.04 -12.08, -77.04 -12.06, -77.06 -12.06, -77.06 -12.08))"
)
DISTRICTS = [
    (150101, "Miraflores"),
    (150102, "San Isidro"),
    (150103, "Barranco"),
    (150104, "Santiago de Surco"),
    (150105, "San Borja"),
    (150106, "Surquillo"),
    (150107, "La Molina"),
    (150108, "Lince"),
    (150109, "Pueblo Libre"),
    (150110, "San Miguel"),
    (150111, "Jesús María"),
    (150112, "Magdalena del Mar"),
    (150113, "La Victoria"),
    (150114, "Breña"),
    (150115, "Lima Cercado"),
    (150116, "El Agustino"),
    (150117, "San Juan de Miraflores"),
    (150118, "San Juan de Lurigancho"),
    (150119, "San Martín de Porres"),
    (150120, "Comas"),
    (150121, "Independencia"),
    (150122, "Rímac"),
    (150123, "San Luis"),
    (150124, "Santa Anita"),
    (150125, "Ate"),
    (150126, "Santa Rosa"),
    (150127, "Punta Hermosa"),
    (150128, "Punta Negra"),
    (150129, "San Bartolo"),
    (150130, "Pucusana"),
    (150131, "Chorrillos"),
    (150132, "Chaclacayo"),
    (150133, "Lurín"),
    (150134, "Pachacámac"),
    (150135, "Cieneguilla"),
    (150136, "Carabayllo"),
    (150137, "Puente Piedra"),
    (150138, "Ancón"),
    (150139, "Santa María del Mar"),
    (150140, "Villa El Salvador"),
    (150141, "Villa María del Triunfo"),
    (150142, "Magdalena Vieja"),
    (150143, "San Juan de Lurigancho")
]

async def seed_districts():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        await conn.execute("DELETE FROM districts")

        insert_sql = (
            "INSERT INTO districts (district_id, district_name, geometry, area_km2, province, region) "
            "VALUES ($1, $2, ST_SetSRID(ST_GeomFromText($3), 4326), $4, $5, $6)"
        )
        for district_id, district_name in DISTRICTS:
            await conn.execute(
                insert_sql,
                district_id,
                district_name,
                DISTRICT_POLYGON_WKT,
                1.0,
                "Lima",
                "Lima Metropolitana",
            )

        print(f"Seeded {len(DISTRICTS)} districts into the districts table.")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(seed_districts())


if __name__ == "__main__":
    main()
