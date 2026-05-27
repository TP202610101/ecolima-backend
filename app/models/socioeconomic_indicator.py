from sqlalchemy import Column, Float, ForeignKey, Integer, SmallInteger
from sqlalchemy.orm import relationship
from app.db.base import Base


class SocioeconomicIndicator(Base):
    __tablename__ = "socioeconomic_indicators"

    soc_id = Column(Integer, primary_key=True, index=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=False, index=True)
    year = Column(SmallInteger, nullable=False)
    population_density = Column(Float, nullable=False)
    total_population = Column(Integer, nullable=False)
    income_stratum = Column(SmallInteger, nullable=False)
    pct_nse_ab = Column(Float, nullable=False)
    pct_nse_c = Column(Float, nullable=False)
    pct_nse_de = Column(Float, nullable=False)
    edu_level_head = Column(Float, nullable=True)
    household_size_avg = Column(Float, nullable=False)
    fuel_expenditure_sol = Column(Float, nullable=True)
    pct_formal_employment = Column(Float, nullable=True)
    pct_internet_access = Column(Float, nullable=True)
    housing_type_apt_pct = Column(Float, nullable=True)
    hacinamiento_idx = Column(Float, nullable=True)
    pct_poverty = Column(Float, nullable=True)

    district = relationship("District", back_populates="socioeconomic_indicators")
