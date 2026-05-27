from sqlalchemy import Column, Float, ForeignKey, Integer, SmallInteger
from sqlalchemy.orm import relationship
from app.db.base import Base


class WasteGeneration(Base):
    __tablename__ = "waste_generation"

    waste_id = Column(Integer, primary_key=True, index=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=False, index=True)
    year = Column(SmallInteger, nullable=False)
    gpc_kg_per_capita_day = Column(Float, nullable=False)
    pct_recyclable = Column(Float, nullable=True)
    pct_organic = Column(Float, nullable=True)
    pct_plastic = Column(Float, nullable=True)
    pct_paper_cardboard = Column(Float, nullable=True)
    pct_glass = Column(Float, nullable=True)
    pct_metal = Column(Float, nullable=True)
    collection_coverage_pct = Column(Float, nullable=True)
    informal_recyclers_count = Column(Integer, nullable=True)
    total_waste_tons_year = Column(Float, nullable=True)

    district = relationship("District", back_populates="waste_generation")
