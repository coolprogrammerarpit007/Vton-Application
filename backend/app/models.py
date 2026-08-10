from sqlalchemy import Column, Integer, BigInteger, String, Text, Enum, DateTime, JSON, ForeignKey, UniqueConstraint,Boolean,DATETIME,DECIMAL
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from .database import Base
from datetime import datetime

class Platform(Base):
    __tablename__ = "platforms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    slug = Column(String(50), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)

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
    
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)

    platform = relationship("Platform", back_populates="aspect_ratios")

class GarmentSegment(Base):
    __tablename__ = "garment_segments"

    id = Column(Integer, primary_key=True, index=True)
    segment_name = Column(String(50), nullable=False)
    categories = Column(JSON, nullable=False)
    
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
    master_prompt = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)

class GenerationControl(Base):
    __tablename__ = "generation_controls"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String(50), unique=True, nullable=False)
    config_value = Column(JSON, nullable=False)
    description = Column(String(255), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)

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
    password = Column(String(255), nullable=True)
    
    full_name = Column(String(100), nullable=True)
    avatar_url = Column(String(255), nullable=True)
    plan_name = Column(String(50), default="PRO", nullable=False)
    
    auth_provider = Column(String(20), default="local", nullable=False)
    google_sub = Column(String(255), unique=True, index=True, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
    
    user_image_url = Column(String(255), nullable=False)
    garment_image_url = Column(String(255), nullable=False)
    result_image_urls = Column(JSON, nullable=True)
    
    fashn_job_id = Column(String(255), nullable=True, index=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="jobs")

class WearCategory(str, enum.Enum):
    MENS = "mens"
    WOMEN = "women"
    KIDS = "kids"

class ClosetItem(Base):
    __tablename__ = "closet_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_path = Column(String(255), nullable=False)   
    label = Column(String(100), default="Untitled Garment")
    
    category = Column(String(50), default=GarmentCategory.TOPS.value, nullable=False)
    wear_category = Column(Enum(WearCategory), default=WearCategory.MENS, nullable=False) 
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class SystemModel(Base):
    __tablename__ = "system_models"

    id = Column(Integer, primary_key=True, index=True)
    model_name = Column(String(100), nullable=False)
    base_image_url = Column(String(255), nullable=False)
    demographic = Column(Enum(TargetDemographic), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class OutfitLayer(str, enum.Enum):
    TOP = "top"
    BOTTOM = "bottom"
    OUTERWEAR = "outerwear"
    ACCESSORY = "accessory"
    FOOTWEAR = "footwear"

class OutfitJob(Base):
    __tablename__ = "outfit_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    person_image_url = Column(String(255)) 
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    fashn_job_id = Column(String(255), nullable=True)
    result_image_url = Column(String(255), nullable=True)
    styling_prompt = Column(String(1024), nullable=True) 
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    garments = relationship("OutfitGarment", back_populates="outfit_job", cascade="all, delete-orphan")

class OutfitGarment(Base):
    __tablename__ = "outfit_garments"

    id = Column(Integer, primary_key=True, index=True)
    outfit_job_id = Column(Integer, ForeignKey("outfit_jobs.id"))
    closet_item_id = Column(Integer, ForeignKey("closet_items.id"))
    layer_category = Column(Enum(OutfitLayer))

    outfit_job = relationship("OutfitJob", back_populates="garments")
    closet_item = relationship("ClosetItem")

class HistoryItem(Base):
    __tablename__ = "history_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    image_url = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User")

class StudioJobType(str, enum.Enum):
    PRODUCT_TO_MODEL = "product_to_model"
    MODEL_CREATE = "model_create"
    MODEL_SWAP = "model_swap"
    IMAGE_TO_VIDEO = "image_to_video"
    BACKGROUND_CHANGE = "background_change"
    FACE_TO_MODEL = "face_to_model"

class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(Enum(StudioJobType), nullable=False, index=True)
    title = Column(String(100), nullable=True)
    prompt_text = Column(Text, nullable=False)
    
    outfit_description = Column(Text, nullable=True)
    background_setting = Column(Text, nullable=True)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

class StudioJob(Base):
    __tablename__ = "studio_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    job_type = Column(Enum(StudioJobType), index=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    
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
    
    admin_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="support_tickets")


# ******************************************* Start Of Subscription Models ***********************************

# --- New Enums for Subscription & Ledger System ---
class UserSubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING = "pending"
    
    
class ResourceKey(str, enum.Enum):
    CREDITS = "credits"
    CLOSET_ITEMS = "closet_items"
    IMAGE_TO_VIDEO = "image_to_video"
    MODEL_CREATION = "model_creation"
    CREATE_MODEL = "create_model"
    
        
class SubscriptionEvent(str, enum.Enum):
    ASSIGNED = "assigned"
    RENEWED = "renewed"
    UPGRADED = "upgraded"
    DOWNGRADED = "downgraded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REACTIVATED = "reactivated"
    USAGE_RESET = "usage_reset"


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    # --- Core Fields ---
    id = Column(Integer, primary_key=True, index=True)
    plan_name = Column(String(50), unique=True, index=True, nullable=False)
    title = Column(String(100), nullable=False)
    price = Column(String(50), nullable=False)
    credits = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # --- Feature Limits & Gatekeeping (From image_aadbb3.png) ---
    closet_limit = Column(Integer, nullable=False, default=10)
    virtual_try_on = Column(Boolean, nullable=False, default=True)
    view_360_mode = Column(Enum('single_image', 'front_back_side', name='view_360_mode_enum'), nullable=False, default='single_image')
    change_background = Column(Boolean, nullable=False, default=True)
    model_swap = Column(Boolean, nullable=False, default=False)
    product_to_model = Column(Boolean, nullable=False, default=True)
    # NEW: Outerwear Feature Flag
    outerwear_enabled = Column(Boolean, nullable=False, default=False)
    
    image_to_video_resolution = Column(String(20), nullable=True)
    image_to_video_max_count = Column(Integer, nullable=True)
    

    # --- Extended Limits & Quality Configs (From image_aadb76.png) ---
    image_to_video_max_seconds = Column(Integer, nullable=False, default=10)
    smart_crop = Column(Boolean, nullable=False, default=True)
    face_to_model = Column(Boolean, nullable=False, default=False)
    create_model_enabled = Column(Boolean, nullable=False, default=False)
    create_model_max = Column(Integer, nullable=True)
    video_quality = Column(Enum('480p', '720p', '1080p', name='video_quality_enum'), nullable=False, default='720p')
    chat_support_enabled = Column(Boolean, nullable=False, default=False)
    chat_support_response_hours = Column(DECIMAL(4, 1), nullable=True)
    model_creation_limit = Column(Integer, nullable=True)
    special_offer = Column(Boolean, nullable=False, default=False)
    early_access = Column(Boolean, nullable=False, default=False)
    image_quality = Column(Enum('2k', '4k', name='image_quality_enum'), nullable=False, default='2k')
    image_retention_hours = Column(Integer, nullable=False, default=24)
    
    

# --- 1. User Subscriptions (Active Plan & Credits) ---
class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    subscription_plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False, index=True)
    
    # CRITICAL FIX: Maps SQLAlchemy to the lowercase database values to prevent LookupError
    status = Column(
        Enum(UserSubscriptionStatus, values_callable=lambda obj: [e.value for e in obj]), 
        default=UserSubscriptionStatus.ACTIVE, 
        nullable=False, 
        index=True
    )
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    
    # NEW: Payment tracking columns
    latest_txnid = Column(String(255), nullable=True)
    latest_payment_amount = Column(String(50), nullable=True)
    latest_payment_date = Column(DateTime, nullable=True)
    
    plan_snapshot = Column(JSON, nullable=False, comment='Limits/features copied at assignment')
    credits_remaining = Column(Integer, default=0, nullable=False)
    
    assigned_by = Column(BigInteger, nullable=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="subscription")
    plan = relationship("SubscriptionPlan")


# --- 2. user_plan_resource_usages ---
# --- 2. user_plan_resource_usages ---
class UserPlanResourceUsage(Base):
    __tablename__ = "user_plan_resource_usages"
    __table_args__ = (
        UniqueConstraint('user_subscription_id', 'resource_key', name='sub_resource_unique'),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_subscription_id = Column(BigInteger, ForeignKey("user_subscriptions.id", ondelete="CASCADE"), nullable=False)
    
    # CRITICAL FIX: Map lowercase DB strings to the Python Enum values
    resource_key = Column(
        Enum(ResourceKey, values_callable=lambda obj: [e.value for e in obj]), 
        nullable=False
    )
    limit_value = Column(Integer, nullable=True, comment='NULL = unlimited')
    used_value = Column(Integer, default=0, nullable=False)
    
    period_starts_at = Column(DateTime, nullable=True)
    period_ends_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    subscription = relationship("UserSubscription", backref="resource_usages")
    
    
# --- 3. user_resource_usage_logs ---
class UserResourceUsageLog(Base):
    __tablename__ = "user_resource_usage_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_subscription_id = Column(BigInteger, ForeignKey("user_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # CRITICAL FIX
    resource_key = Column(
        Enum(ResourceKey, values_callable=lambda obj: [e.value for e in obj]), 
        nullable=False, 
        index=True
    )
    delta = Column(Integer, nullable=False, comment='Positive = consume, negative = refund')
    used_after = Column(Integer, nullable=False)
    limit_at_time = Column(Integer, nullable=True)
    
    reference_type = Column(String(255), nullable=True)
    reference_id = Column(BigInteger, nullable=True)
    description = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    
# --- 4. user_subscription_histories ---
class UserSubscriptionHistory(Base):
    __tablename__ = "user_subscription_histories"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_subscription_id = Column(BigInteger, ForeignKey("user_subscriptions.id", ondelete="SET NULL"), nullable=True)
    
    subscription_plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    previous_subscription_plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=True)
    
    # CRITICAL FIX: Map lowercase event strings (e.g. 'assigned', 'upgraded')
    event = Column(
        Enum(SubscriptionEvent, values_callable=lambda obj: [e.value for e in obj]), 
        nullable=False
    )
    plan_snapshot = Column(JSON, nullable=False)
    credits_at_event = Column(Integer, nullable=True)
    
    event_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, index=True)
    effective_from = Column(DateTime, nullable=True)
    effective_until = Column(DateTime, nullable=True)
    
    meta_data = Column("meta", JSON, nullable=True)
    created_by = Column(BigInteger, nullable=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# --- User Billing Details ---
class UserBillingDetail(Base):
    __tablename__ = "user_billing_details"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    full_name = Column(String(150), nullable=False)
    email = Column(String(150), nullable=False)
    phone_number = Column(String(20), nullable=False)
    
    address_line_1 = Column(String(255), nullable=True)
    address_line_2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    pincode = Column(String(20), nullable=False)
    
    gst_number = Column(String(50), nullable=True)
    company_name = Column(String(150), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="billing_detail")



    
    
    
    
# **********************************************************************************************************

# ******************  End Of Subscription ******************************************************************



# ******************************************* Models for the Payment Integration   ****************************

class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    TAMPERED = "tampered"

class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True, index=True)
    txnid = Column(String(255), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    amount = Column(String(50), nullable=False)
    firstname = Column(String(100), nullable=False)
    email = Column(String(150), nullable=False)
    phone = Column(String(20), nullable=False)
    product_info = Column(String(255), nullable=False)
    
    payu_money_id = Column(String(255), nullable=True)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING, nullable=False)
    raw_response = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="payment_transactions")
    
    
# ***************** Topup Options Table *****************************************
class TopupOption(Base):
    __tablename__ = "topup_options"

    id = Column(BigInteger, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    credits = Column(DECIMAL, nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    description = Column(Text, nullable=False)

# *********************************************** End *********************************************************
    
    
    
    
class FAQ(Base):
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String(255), nullable=False)
    answer = Column(Text, nullable=False)
    
    # Allows you to hide an FAQ without deleting it from the database
    is_active = Column(Boolean, default=True, nullable=False) 
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())