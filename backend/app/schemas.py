from pydantic import BaseModel, EmailStr,constr
from datetime import datetime
from typing import Optional
from app.models import GarmentCategory, JobStatus

# --- User Schemas ---
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: constr(min_length=8, max_length=72)

class UserOut(BaseModel):
    id: int
    username: str
    email: EmailStr
    created_at: datetime

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