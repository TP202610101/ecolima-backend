from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from app.db.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    alert_id = Column(Integer, primary_key=True, autoincrement=True)
    district_id = Column(Integer, ForeignKey("districts.district_id"), nullable=False, index=True)
    # % de zonas recomendadas por el modelo que YA tienen un punto de reciclaje
    # real a <=500m (redundancia entre recomendación y cobertura existente).
    # NO mide llenado físico de contenedores -- no hay ese dato en el sistema.
    redundancy_pct = Column(Float, nullable=False)
    status = Column(String(10), nullable=False, default="active")  # active | resolved
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
