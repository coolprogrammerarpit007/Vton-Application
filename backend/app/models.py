from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class GarmentCategory(str, enum.Enum):
    TOPS = "tops"
    BOTTOMS = "bottoms"
    ONE_PIECES = "one-pieces"

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to track all try-on requests made by this user
    jobs = relationship("TryOnJob", back_populates="user")

class TryOnJob(Base):
    __tablename__ = "tryon_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(Enum(GarmentCategory), nullable=False)
    
    # Storage URLs for processing images
    user_image_url = Column(String(255), nullable=False)
    garment_image_url = Column(String(255), nullable=False)
    result_image_url = Column(String(255), nullable=True)
    
    # Tracking parameters for the third-party FASHN.ai system
    fashn_job_id = Column(String(100), nullable=True, index=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="jobs")
    
    
class ClosetItem(Base):
    __tablename__ = "closet_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id")) # Links to your existing users table
    file_path = Column(String(255), nullable=False)   # Stores where the image lives on the server
    label = Column(String(100), default="Untitled Garment")
    created_at = Column(DateTime(timezone=True), server_default=func.now())