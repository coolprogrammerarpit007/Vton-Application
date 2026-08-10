import re
from datetime import datetime
from typing import Any, Optional, List, Dict
from pydantic import BaseModel, EmailStr, Field, field_validator
from .models import GarmentCategory, JobStatus, StudioJobType, TicketStatus, TicketPriority, UserSubscription, ResourceKey, SubscriptionEvent, UserSubscriptionStatus

# --- User Schemas ---
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, strip_whitespace=True)
    email: EmailStr 
    password: str = Field(..., min_length=8)

    @field_validator('username')
    def validate_username(cls, v):
        if not re.match(r"^\w+$", v):
            raise ValueError("Username can only contain letters, numbers, and underscores")
        return v

class UserLogin(BaseModel):
    email: EmailStr 
    password: str = Field(...)
    
    
class StandardResponse(BaseModel):
    status: bool
    msg: str
    data: Optional[Any] = None
    class Config:
        from_attributes = True
        
        
class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    reset_token: str
    new_password: str
    
class GoogleLoginPayload(BaseModel):
    sub: str
    name: str
    given_name: Optional[str] = None
    picture: Optional[str] = None
    email: EmailStr
    email_verified: bool
    
    
    
# --- Subscription Plans ---
class SubscriptionPlanResponse(BaseModel):
    # --- Core Fields ---
    id: int
    plan_name: str
    title: str
    price: str
    credits: int
    is_active: bool
    
    # --- Feature Limits & Gatekeeping ---
    closet_limit: int
    virtual_try_on: bool
    view_360_mode: str
    change_background: bool
    model_swap: bool
    product_to_model: bool
    outerwear_enabled: bool  # NEW: Added to schema
    image_to_video_resolution: Optional[str]
    image_to_video_max_count: Optional[int]

    # --- Extended Limits & Quality Configs ---
    image_to_video_max_seconds: int
    smart_crop: bool
    face_to_model: bool
    create_model_enabled: bool
    create_model_max: Optional[int]
    video_quality: str
    chat_support_enabled: bool
    chat_support_response_hours: Optional[float] 
    model_creation_limit: Optional[int]
    special_offer: bool
    early_access: bool
    image_quality: str
    image_retention_hours: int

    # --- Timestamps ---
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

class StandardSubscriptionPlanListResponse(BaseModel):
    status: bool
    msg: str
    has_billing_details: bool = False  # NEW: Top-level boolean flag
    data: List[SubscriptionPlanResponse]
# --- Payment Schemas ---
class PaymentInitiateRequest(BaseModel):
    plan_name: str = Field(..., description="E.g., silver, gold, platinum")
    phone: str = Field(default="9351469994", min_length=10, max_length=15)

class PaymentInitiateResponseData(BaseModel):
    action_url: str
    payment_data: Dict[str, Any]

class StandardPaymentResponse(BaseModel):
    status: bool
    msg: str
    data: Optional[PaymentInitiateResponseData] = None
    
    
class TicketCreate(BaseModel):
    subject: str
    description: str
    priority: Optional[TicketPriority] = TicketPriority.MEDIUM

class TicketUpdateStatus(BaseModel):
    status: TicketStatus
    admin_notes: Optional[str] = None

class TicketResponse(BaseModel):
    id: int
    user_id: int
    subject: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    admin_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StandardTicketResponse(BaseModel):
    status: bool
    msg: str
    data: Optional[TicketResponse] = None

class StandardTicketListResponse(BaseModel):
    status: bool
    msg: str
    data: List[TicketResponse]
    
class Token(BaseModel):
    access_token: str
    token_type: str
    
    class Config:
        from_attributes = True

# --- Try-On Job Schemas ---
class TryOnJobCreate(BaseModel):
    user_id: int
    category: GarmentCategory

class TryOnJobOut(BaseModel):
    id: int
    user_id: int
    category: GarmentCategory
    user_image_url: str
    garment_image_url: str
    result_image_url: Optional[str] = None
    status: JobStatus
    created_at: datetime

    class Config:
        from_attributes = True
        

    
class PromptTemplateItem(BaseModel):
    id: int
    job_type: StudioJobType
    title: Optional[str]
    prompt_text: str
    outfit_description: Optional[str] = None
    background_setting: Optional[str] = None
    
    class Config:
        from_attributes = True

class PromptTemplateResponse(StandardResponse):
    data: List[PromptTemplateItem]
    
    
# Represents a single FAQ in the response
class FAQResponse(BaseModel):
    id: int
    question: str
    answer: str
    is_active: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

# Standardized wrapper for the frontend
class StandardFAQListResponse(BaseModel):
    status: bool
    msg: str
    data: List[FAQResponse]

# Input schema for seeding/creating FAQs
class FAQCreate(BaseModel):
    question: str
    answer: str

    

# ************************ Subscription Models Responses ******************************
# --- Resource Usage Schema ---
class UserPlanResourceUsageResponse(BaseModel):
    id: int
    resource_key: ResourceKey
    limit_value: Optional[int]
    used_value: int
    period_starts_at: Optional[datetime]
    period_ends_at: Optional[datetime]
    
    class Config:
        from_attributes = True

# --- Active Subscription State Schema ---
class UserSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    subscription_plan_id: int
    status: UserSubscriptionStatus
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    
    latest_txnid: Optional[str]
    latest_payment_amount: Optional[str]
    latest_payment_date: Optional[datetime]
    
    credits_remaining: int
    notes: Optional[str]
    plan_snapshot: Dict[str, Any]
    
    resource_usages: List[UserPlanResourceUsageResponse] = []

    class Config:
        from_attributes = True

class StandardUserSubscriptionResponse(BaseModel):
    status: bool
    msg: str
    data: Optional[UserSubscriptionResponse] = None

# --- Ledger / Transaction Log Schema ---
class UserResourceUsageLogResponse(BaseModel):
    id: int
    resource_key: ResourceKey
    delta: int
    used_after: int
    limit_at_time: Optional[int]
    reference_type: Optional[str]
    reference_id: Optional[int]
    description: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

class StandardLedgerHistoryResponse(BaseModel):
    status: bool
    msg: str
    data: List[UserResourceUsageLogResponse]

# --- Subscription History Schema ---
class UserSubscriptionHistoryResponse(BaseModel):
    id: int
    subscription_plan_id: int
    previous_subscription_plan_id: Optional[int]
    event: SubscriptionEvent
    credits_at_event: Optional[int]
    event_at: datetime
    effective_from: Optional[datetime]
    effective_until: Optional[datetime]
    meta_data: Optional[Dict[str, Any]] = None
    
    class Config:
        from_attributes = True
# *************************************************************************************



# ************************* Schemas for Billing Details ******************************

# --- Billing Detail  and Purchase history Schemas ---
class BillingDetailCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    phone_number: str = Field(..., min_length=10, max_length=20)
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: str = Field(..., min_length=2, max_length=100)
    state: str = Field(..., min_length=2, max_length=100)
    pincode: str = Field(..., min_length=4, max_length=20)
    gst_number: Optional[str] = None
    company_name: Optional[str] = None

class BillingDetailResponse(BillingDetailCreate):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
        
        
# --- Payment History Schemas ---
class PaymentHistoryItem(BaseModel):
    transaction_id: str
    plan_name: str
    purchase_amount: str
    purchase_date: str      # Changed from datetime to str
    validation_date: str    # Changed from datetime to str
    credits_purchased: int

class PaymentHistoryListResponse(BaseModel):
    status: bool
    msg: str
    data: List[PaymentHistoryItem]
    
    
# --- Top-Up Schemas ---
class PaymentTopupInitiateRequest(BaseModel):
    credits: int = Field(..., ge=1, description="Number of credits to purchase, e.g., 10")
    amount: float = Field(..., ge=1.0, description="Amount in INR, e.g., 100.00")
    # phone: str = Field(default="9351469994", min_length=10, max_length=15)

class TopupOptionItem(BaseModel):
    id: int
    title: str
    credits: int
    amount: float
    description: str

class StandardTopupOptionsResponse(BaseModel):
    status: bool
    msg: str
    credit_rate:int
    data: List[TopupOptionItem]
        
        
# ***************************************************************************************