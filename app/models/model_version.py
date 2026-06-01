from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from app.db.base import Base


class ModelVersion(Base):
    __tablename__ = "model_versions"

    version_id   = Column(Integer, primary_key=True, autoincrement=True)
    version_name = Column(String(20), unique=True, nullable=False, index=True)
    training_date = Column(DateTime, nullable=True)
    trained_by   = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    artifact_url  = Column(String(500), nullable=True)
    # JSON: {"accuracy": 0.94, "auc_pr": 0.71, "f1": 0.68, "precision": 0.72, "recall": 0.65}
    metrics      = Column(Text, nullable=True)
    # JSON array con los nombres de las 20 features usadas
    features_used = Column(Text, nullable=True)
    is_active    = Column(Boolean, nullable=False, default=False)
    created_at   = Column(DateTime, default=datetime.utcnow)
