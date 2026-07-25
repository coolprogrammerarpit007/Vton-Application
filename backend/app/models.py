from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DateTime, Boolean, func,JSON  
from app.database import Base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from datetime import datetime




    
# Unified Demographic Enum
class TargetDemographic(str, enum.Enum):
    MAN = "man"
    WOMAN = "woman"
    KIDS = "kids"
    

class GarmentCategory(str, enum.Enum):
    TOPS = "tops"
    BOTTOMS = "bottoms"
    ONE_PIECES = "one-pieces"

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    
class MasterModuleType(str, enum.Enum):
    TRYON = "tryon"
    THREE_SIXTY = "three-sixty"
    OUTFIT = "outfit"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    
    # NEW PROFILE COLUMNS ADDED
    full_name = Column(String(100), nullable=True)
    avatar_url = Column(String(255), nullable=True)
    plan_name = Column(String(50), default="PRO", nullable=False)
    
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
    # result_image_url = Column(String(255), nullable=True)
    result_image_urls = Column(JSON, nullable=True)
    
    # Tracking parameters for the third-party FASHN.ai system
    fashn_job_id = Column(String(255), nullable=True, index=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="jobs")
    
    
# models.py
class ClosetItem(Base):
    __tablename__ = "closet_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_path = Column(String(255), nullable=False)   
    label = Column(String(100), default="Untitled Garment")
    
    
    category = Column(String(50), default=GarmentCategory.TOPS.value, nullable=False)
    
    # demographic = Column(Enum(TargetDemographic), default=TargetDemographic.WOMAN, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
# Dedicated System Model Catalog Table
class SystemModel(Base):
    __tablename__ = "system_models"

    id = Column(Integer, primary_key=True, index=True)
    model_name = Column(String(100), nullable=False)
    base_image_url = Column(String(255), nullable=False)
    demographic = Column(Enum(TargetDemographic), nullable=False)
    is_active = Column(Boolean, default=True)
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
    
   
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
    
    
class StudioJobType(str, enum.Enum):
    PRODUCT_TO_MODEL = "product_to_model"
    MODEL_CREATE = "model_create"
    MODEL_SWAP = "model_swap"
    IMAGE_TO_VIDEO = "image_to_video"
    BACKGROUND_CHANGE = "background_change"
    FACE_TO_MODEL = "face_to_model"
    
    
class StudioJob(Base):
    __tablename__ = "studio_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    job_type = Column(Enum(StudioJobType), index=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    
    # ✅ FIXED: Changed to allow null values while the background task runs
    fashn_job_id = Column(String(255), nullable=True) 
    
    input_data = Column(JSON, default={})
    result_urls = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())