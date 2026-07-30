from sqlalchemy import Column, Integer, String,Boolean,Text, Enum, ForeignKey, DateTime, Boolean, func,JSON  
from app.database import Base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from datetime import datetime

class Platform(Base):
    __tablename__ = "platforms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    slug = Column(String(50), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)

    # New timestamp columns
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)

    aspect_ratios = relationship(
        "AspectRatio", back_populates="platform", cascade="all, delete-orphan"
    )
    
    
class AspectRatio(Base):
    __tablename__ = "aspect_ratios"

    id = Column(Integer, primary_key=True, index=True)
    platform_id = Column(Integer, ForeignKey("platforms.id"), nullable=False)
    ratio = Column(String(10), nullable=False)
    default_width = Column(Integer, nullable=False)
    default_height = Column(Integer, nullable=False)
    is_default = Column(Boolean, default=False)
    
    # New timestamp columns
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)

    platform = relationship("Platform", back_populates="aspect_ratios")
    
    
class GarmentSegment(Base):
    __tablename__ = "garment_segments"

    id = Column(Integer, primary_key=True, index=True)
    segment_name = Column(String(50), nullable=False)
    categories = Column(JSON, nullable=False)
    
    # New timestamp columns
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)
    
class ModelPersona(Base):
    __tablename__ = "model_personas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(20), nullable=False)
    preview_image_url = Column(String(255), nullable=False)
    attributes_json = Column(JSON, nullable=False)
    master_prompt = Column(Text, nullable=False)  # Kept internal only
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now()),
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)
    
    
class GenerationControl(Base):
    __tablename__ = "generation_controls"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String(50), unique=True, nullable=False)
    config_value = Column(JSON, nullable=False)
    description = Column(String(255), nullable=True)
    
    # New timestamp columns
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)


    
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
    hashed_password = Column(String(255), nullable=True)
    
    # NEW PROFILE COLUMNS ADDED
    full_name = Column(String(100), nullable=True)
    avatar_url = Column(String(255), nullable=True)
    plan_name = Column(String(50), default="PRO", nullable=False)
    
    # Tracks login method: "local" or "google"
    auth_provider = Column(String(20), default="local", nullable=False)
    
    # NEW: Store Google's unique subject identifier
    google_sub = Column(String(255), unique=True, index=True, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to track all try-on requests made by this user
    jobs = relationship("TryOnJob", back_populates="user")



class TryonPromptPreset(Base):
    __tablename__ = "tryon_prompt_presets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), nullable=False)
    prompt_text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)
    
    

    
    
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
    
    
# Add this near your other Enums in models.py
class WearCategory(str, enum.Enum):
    MENS = "mens"
    WOMEN = "women"
    KIDS = "kids"
    
# models.py
class ClosetItem(Base):
    __tablename__ = "closet_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_path = Column(String(255), nullable=False)   
    label = Column(String(100), default="Untitled Garment")
    
    category = Column(String(50), default=GarmentCategory.TOPS.value, nullable=False)
    
    # NEW REQUIRED FIELD (Added a default to prevent crashes with existing DB rows)
    wear_category = Column(Enum(WearCategory), default=WearCategory.MENS, nullable=False) 
    
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
    
    
# NEW MODEL: To store dynamic prompts
class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(Enum(StudioJobType), nullable=False, index=True)
    title = Column(String(100), nullable=True)  # A short name for the UI (e.g., "Cinematic Studio")
    prompt_text = Column(Text, nullable=False)  # The actual prompt string
    
    # --- NEW COLUMNS ---
    outfit_description = Column(Text, nullable=True)
    background_setting = Column(Text, nullable=True)
    
    is_active = Column(Boolean, default=True)   # Allows admin to disable prompts without deleting
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    
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
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    
    
    
class PasswordResetOTP(Base):
    __tablename__ = "password_reset_otps"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    otp = Column(String(6), nullable=False)
    reset_token = Column(String(255), nullable=True, index=True)
    is_used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    
    
    
#  Customer Support Platform APIs

class TicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    
class TicketPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    
    
class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    subject = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    
    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN, index=True)
    priority = Column(Enum(TicketPriority), default=TicketPriority.MEDIUM)
    
    admin_notes = Column(Text, nullable=True) # For internal platform use
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Assuming you have a User model defined
    user = relationship("User", backref="support_tickets")