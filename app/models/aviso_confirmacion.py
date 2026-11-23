from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.db.base import Base


class AvisoConfirmacion(Base):
    """Confirmación de lectura de un aviso cuyo contenido vive en el
    frontend -- acá solo se guarda la clave, la versión confirmada y cuándo.
    Una fila por (user_id, clave, version); si la versión cambia, la
    confirmación anterior sigue existiendo pero ya no cuenta para la nueva."""

    __tablename__ = "aviso_confirmaciones"
    __table_args__ = (
        UniqueConstraint("user_id", "clave", "version", name="uq_aviso_confirmaciones_user_clave_version"),
    )

    confirmacion_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    clave = Column(String(50), nullable=False)
    version = Column(String(20), nullable=False)
    confirmado_en = Column(DateTime, nullable=False, default=datetime.utcnow)
