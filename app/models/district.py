from sqlalchemy import Column, Float, Integer, String
from sqlalchemy.orm import relationship
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

    socioeconomic_indicators = relationship("SocioeconomicIndicator", back_populates="district")
    waste_generation = relationship("WasteGeneration", back_populates="district")
    recycling_points = relationship("RecyclingPoint", back_populates="district")
    candidate_zones = relationship("CandidateZone", back_populates="district")
