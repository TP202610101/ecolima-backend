from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text, ForeignKey
from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    action = Column(String(50), nullable=False)
    # ondelete="SET NULL": si el dataset se borra (DELETE /datasets/{id}), su
    # historial de auditoría NO se borra -- queda con dataset_id=NULL, como
    # evidencia histórica desligada. Ya estaba así en la BD real desde la
    # migración inicial (001_initial.py); esto solo hace que el modelo ORM
    # coincida con la restricción real.
    dataset_id = Column(Integer, ForeignKey("datasets.dataset_id", ondelete="SET NULL"), nullable=True, index=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
