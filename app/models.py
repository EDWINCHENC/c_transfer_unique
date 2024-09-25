# app/models.py

from sqlalchemy import Column, Integer, String, DateTime
from .database import Base
from datetime import datetime, timezone, timedelta

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, index=True)
    content = Column(String)
    filename = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone(timedelta(hours=8))))
    access_code = Column(String, index=True)
    creator_ip = Column(String, index=True)  # 新增字段记录创建者IP

    def get_created_at(self):
        return self.created_at

class FileAccess(Base):
    __tablename__ = "file_access"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    access_code = Column(String, index=True)
