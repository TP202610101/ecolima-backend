from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from app.db.base import Base


class CandidateZone(Base):
    __tablename__ = "candidate_zones"

    zone_id = Column(Integer, primary_key=True, index=True)
    centroid_lat = Column(Float, nullable=False)
    centroid_lon = Column(Float, nullable=False)
    geometry = Column(Geometry("POLYGON", srid=4326), nullable=False)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=False, index=True)
    cell_area_km2 = Column(Float, nullable=False)

    population_density = Column(Float, nullable=True)
    income_stratum = Column(SmallInteger, nullable=True)
    fuel_expenditure_sol = Column(Float, nullable=True)
    edu_level_head = Column(Float, nullable=True)
    household_size_avg = Column(Float, nullable=True)
    pct_nse_ab = Column(Float, nullable=True)
    pct_nse_de = Column(Float, nullable=True)
    gpc_kg_per_capita_day = Column(Float, nullable=True)
    pct_recyclable = Column(Float, nullable=True)
    pct_plastic = Column(Float, nullable=True)
    informal_recyclers_count = Column(Integer, nullable=True)
    road_density = Column(Float, nullable=True)
    dist_to_main_road_m = Column(Float, nullable=True)
    dist_to_market_m = Column(Float, nullable=True)
    dist_to_nearest_point_m = Column(Float, nullable=True)
    existing_points_500m = Column(Integer, nullable=False, default=0)
    has_park_300m = Column(Boolean, nullable=False, default=False)
    slope_pct = Column(Float, nullable=True)
    urbanized_area_pct = Column(Float, nullable=True)
    recycling_potential_index = Column(Float, nullable=True)

    is_suitable = Column(SmallInteger, nullable=True)

    ml_score = Column(Float, nullable=True)
    is_recommended = Column(Boolean, nullable=True)
    priority_label = Column(String(10), nullable=True)
    recommendation_reason = Column(Text, nullable=True)
    coverage_gap_m = Column(Float, nullable=True)
    model_version = Column(String(20), nullable=True)
    inference_date = Column(DateTime, nullable=True)

    district = relationship("District", back_populates="candidate_zones")
