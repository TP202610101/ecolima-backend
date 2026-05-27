from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text, ForeignKey
from app.db.base import Base


class Dataset(Base):
    __tablename__ = "datasets"

    dataset_id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    file_size_bytes = Column(Integer, nullable=True)
    row_count = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    detected_columns = Column(Text, nullable=True)
    error_summary = Column(Text, nullable=True)
