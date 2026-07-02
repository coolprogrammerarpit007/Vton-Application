from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from . import models
from .database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/history", tags=["History"])

# Pydantic schema for the incoming request
class HistoryCreate(BaseModel):
    image_url: str

# Pydantic schema for the outgoing response
class HistoryResponse(BaseModel):
    id: int
    image_url: str
    created_at: datetime

    class Config:
        orm_mode = True

@router.post("/save")
async def save_to_history(
    item: HistoryCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    """Saves a generated image URL to the user's history."""
    db_item = models.HistoryItem(
        user_id=current_user.id,
        image_url=item.image_url
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return {"message": "Image saved to history successfully", "id": db_item.id}

@router.get("/", response_model=List[HistoryResponse])
async def get_history(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    """Fetches all history items for the logged-in user, newest first."""
    items = db.query(models.HistoryItem)\
              .filter(models.HistoryItem.user_id == current_user.id)\
              .order_by(models.HistoryItem.created_at.desc())\
              .all()
    return items