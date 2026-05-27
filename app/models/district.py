from sqlalchemy import Column, Float, Integer, String
from geoalchemy2 import Geometry
from app.db.base import Base


class District(Base):
    __tablename__ = "districts"

    district_id = Column(Integer, primary_key=True, index=True)
    district_name = Column(String(100), nullable=False)
    geometry = Column(Geometry("POLYGON", srid=4326), nullable=False)
    area_km2 = Column(Float, nullable=True)
    province = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
