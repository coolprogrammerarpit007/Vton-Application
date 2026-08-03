from pydantic import BaseModel,EmailStr,Field,field_validator
from typing   import Any,Optional,List,Dict
from datetime import datetime
import re
from app.models import GarmentCategory, JobStatus,StudioJobType,TicketStatus, TicketPriority

# --- User Schemas ---
class UserCreate(BaseModel):
    # Enforce minimum and maximum lengths, strip whitespace
    username: str = Field(..., min_length=3, max_length=50, strip_whitespace=True)
    
    # EmailStr automatically validates proper email formatting (e.g., user@domain.com)
    email: EmailStr 
    
    # Enforce minimum password length
    password: str = Field(..., min_length=8)

    # Optional: Ensure username doesn't contain spaces or weird characters
    @field_validator('username')
    def validate_username(cls, v):
        if not re.match(r"^\w+$", v):
            raise ValueError("Username can only contain letters, numbers, and underscores")
        return v

class UserLogin(BaseModel):
    # EmailStr guarantees it looks like a real email address
    email: EmailStr 
    
    # min_length=1 prevents users from submitting empty strings ("")
    password: str = Field(...)

class Token(BaseModel):
    access_token: str
    token_type: str
    
    
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

# Assuming you have a StandardResponse schema like in your previous code
class StandardTicketResponse(BaseModel):
    status: bool
    msg: str
    data: Optional[TicketResponse] = None

class StandardTicketListResponse(BaseModel):
    status: bool
    msg: str
    data: List[TicketResponse]
    
class StandardResponse(BaseModel):
    status:bool
    msg:str
    data:Optional[Any] = None
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
    
    
    
    
# Subscription Plans

class SubscriptionPlanResponse(BaseModel):
    id: int
    plan_name: str
    title: str
    price: str
    credits: int
    is_active: bool

    class Config:
        from_attributes = True

class StandardSubscriptionPlanListResponse(BaseModel):
    status: bool
    msg: str
    data: List[SubscriptionPlanResponse]
    
    
# Adding Schema of Payment Transaction

# --- Updated Payment Schemas ---

class PaymentInitiateRequest(BaseModel):
    # Frontend only sends the plan name and phone number
    plan_name: str = Field(..., description="E.g., silver, gold, platinum")
    phone: str = Field(default="9351469994", min_length=10, max_length=15)

class PaymentInitiateResponseData(BaseModel):
    action_url: str
    payment_data: Dict[str, Any]

class StandardPaymentResponse(BaseModel):
    status: bool
    msg: str
    data: Optional[PaymentInitiateResponseData] = None
    
    

    
    
    
