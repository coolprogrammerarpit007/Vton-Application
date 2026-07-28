from pydantic import BaseModel,EmailStr,Field,field_validator
from typing   import Any,Optional
from datetime import datetime
import re
from app.models import GarmentCategory, JobStatus

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