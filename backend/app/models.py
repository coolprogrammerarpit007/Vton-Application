from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from datetime import datetime
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
    
    
    
    
# 1. Define the Layer Categories
class OutfitLayer(str, enum.Enum):
    TOP = "top"
    BOTTOM = "bottom"
    OUTERWEAR = "outerwear"
    ACCESSORY = "accessory"
    FOOTWEAR = "footwear"
    
    
# 2. Parent Table: Tracks the generation request
class OutfitJob(Base):
    __tablename__ = "outfit_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    # ADDED LENGTH LIMITS TO STRINGS
    person_image_url = Column(String(255)) 
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    fashn_job_id = Column(String(255), nullable=True)
    result_image_url = Column(String(255), nullable=True)
    
    # Gave the prompt a bit more room just in case it gets detailed
    styling_prompt = Column(String(1024), nullable=True) 

    # Links to the multiple garments
    garments = relationship("OutfitGarment", back_populates="outfit_job", cascade="all, delete-orphan")

# 3. Child Table: Maps closet items to the specific job
class OutfitGarment(Base):
    __tablename__ = "outfit_garments"

    id = Column(Integer, primary_key=True, index=True)
    outfit_job_id = Column(Integer, ForeignKey("outfit_jobs.id"))
    closet_item_id = Column(Integer, ForeignKey("closet_items.id"))
    layer_category = Column(Enum(OutfitLayer))

    outfit_job = relationship("OutfitJob", back_populates="garments")
    closet_item = relationship("ClosetItem") # Allows us to fetch the actual file path later
    
    
    
class HistoryItem(Base):
    __tablename__ = "history_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    image_url = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Establish relationship to the User model if needed
    owner = relationship("User")