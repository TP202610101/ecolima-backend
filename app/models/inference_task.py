from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text
from app.db.base import Base


class InferenceTask(Base):
    __tablename__ = "inference_tasks"

    task_id = Column(String(36), primary_key=True)
    status = Column(String(20), nullable=False, default="running")  # running/done/error
    progress_pct = Column(Integer, nullable=False, default=0)
    zones_processed = Column(Integer, nullable=False, default=0)
    # JSON: {"high_priority": N, "medium_priority": N, "low_priority": N} si status="done",
    # o {"error": "mensaje"} si status="error". NULL mientras status="running".
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
