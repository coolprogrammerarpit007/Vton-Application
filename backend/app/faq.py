from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from . import models, schemas
from .database import get_db

router = APIRouter(prefix="/api/faqs", tags=["FAQs"])

@router.get("", response_model=schemas.StandardFAQListResponse)
def get_all_active_faqs(db: Session = Depends(get_db)):
    """
    Fetch all active FAQs to display on the frontend support page.
    """
    faqs = db.query(models.FAQ).filter(models.FAQ.is_active == True).all()
    
    return schemas.StandardFAQListResponse(
        status=True,
        msg="FAQs retrieved successfully.",
        data=faqs
    )

@router.post("/seed", response_model=schemas.StandardFAQListResponse)
def seed_faqs(faq_list: List[schemas.FAQCreate], db: Session = Depends(get_db)):
    """
    Admin endpoint to quickly populate the database with an array of FAQs.
    """
    created_faqs = []
    
    for item in faq_list:
        new_faq = models.FAQ(
            question=item.question,
            answer=item.answer
        )
        db.add(new_faq)
        created_faqs.append(new_faq)
        
    db.commit()
    
    # Refresh to grab IDs and timestamps
    for faq in created_faqs:
        db.refresh(faq)
        
    return schemas.StandardFAQListResponse(
        status=True,
        msg=f"Successfully seeded {len(created_faqs)} FAQs.",
        data=created_faqs
    )