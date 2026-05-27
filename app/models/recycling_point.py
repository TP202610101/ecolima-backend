from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from geoalchemy2 import Geometry
from app.db.base import Base


class RecyclingPoint(Base):
    __tablename__ = "recycling_points"

    point_id = Column(Integer, primary_key=True, index=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    geometry = Column(Geometry("POINT", srid=4326), nullable=False)
    point_type = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    operator = Column(String(100), nullable=True)
    materials_accepted = Column(Text, nullable=True)
    verified = Column(Boolean, nullable=False, default=False)
    source = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
