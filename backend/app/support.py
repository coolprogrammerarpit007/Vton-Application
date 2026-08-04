from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/support", tags=["Support"])

# ==========================================
# 1. CREATE COMPLAINT / TICKET (User)
# ==========================================
@router.post("/tickets", response_model=schemas.StandardTicketResponse)
def create_ticket(
    payload: schemas.TicketCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        new_ticket = models.SupportTicket(
            user_id=current_user.id,
            subject=payload.subject,
            description=payload.description,
            priority=payload.priority
        )
        db.add(new_ticket)
        db.commit()
        db.refresh(new_ticket)
        
        return schemas.StandardTicketResponse(
            status=True,
            msg="Support ticket created successfully.",
            data=new_ticket
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create ticket: {str(e)}")

# ==========================================
# 2. GET COMPLAINTS / TICKETS (User & Admin)
# ==========================================
@router.get("/tickets", response_model=schemas.StandardTicketListResponse)
def get_tickets(
    status_filter: Optional[models.TicketStatus] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        query = db.query(models.SupportTicket)
            
        if status_filter:
            query = query.filter(models.SupportTicket.status == status_filter)
            
        tickets = query.order_by(models.SupportTicket.created_at.desc()).all()
        
        return schemas.StandardTicketListResponse(
            status=True,
            msg="Tickets retrieved successfully.",
            data=tickets
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve tickets: {str(e)}")

# ==========================================
# 3. UPDATE TICKET STATUS (Admin Only)
# ==========================================
@router.patch("/tickets/{ticket_id}/status", response_model=schemas.StandardTicketResponse)
def update_ticket_status(
    ticket_id: int,
    payload: schemas.TicketUpdateStatus,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        ticket = db.query(models.SupportTicket).filter(models.SupportTicket.id == ticket_id).first()
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found.")
            
        ticket.status = payload.status
        if payload.admin_notes is not None:
            ticket.admin_notes = payload.admin_notes
            
        db.commit()
        db.refresh(ticket)
        
        return schemas.StandardTicketResponse(
            status=True,
            msg="Ticket status updated successfully.",
            data=ticket
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update ticket: {str(e)}")