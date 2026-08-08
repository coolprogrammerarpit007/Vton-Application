import os
import uuid
import logging
from typing import List
from logging.handlers import TimedRotatingFileHandler

from fastapi import APIRouter, Depends, UploadFile, File, Form,Path
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
import shutil

# Import your database models, schemas, configs, and session dependency
from . import models
from .database import get_db
from .schemas import StandardResponse
from .auth import get_current_user
from .config import settings  # ADDED: Import settings for dynamic backend URL
from app.exceptions import APIException
from .gatekeeper import PlanGatekeeper # Subscription Dependency Injection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/closet", tags=["Closet"])

UPLOAD_DIR = "static_uploads/closet"
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_FILE_SIZE = 1 * 1024 * 1024  # 2 Megabytes


# A synchronous helper function designed to be run in a threadpool
def write_file_to_disk(upload_file: UploadFile, dest_path: str):
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

@router.post("/upload", response_model=StandardResponse)
async def upload_closet_item(
    category: models.GarmentCategory = Form(models.GarmentCategory.TOPS),
    wear_category: models.WearCategory = Form(...), 
    label: str = Form("My Garment"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    # Gatekeeper: Ensures active subscription and provides plan_snapshot
    subscription: models.UserSubscription = Depends(PlanGatekeeper())
):
    """
    Upload closet garments with strict quota limit enforcement (Silver: 10, Gold: 20, Platinum: 30).
    """
    logger.info(f"Batch closet upload attempt by user ID: {subscription.user_id} | Items: {len(files)}")
    
    # --- 1. ENFORCE CLOSET CAPACITY LIMIT ---
    closet_limit = subscription.plan_snapshot.get("closet_limit", 10)
    current_count = db.query(models.ClosetItem).filter(
        models.ClosetItem.user_id == subscription.user_id
    ).count()

    if current_count + len(files) > closet_limit:
        raise APIException(
            status_code=403, 
            msg=f"Closet storage capacity exceeded. Your plan limit is {closet_limit} items (you currently have {current_count} items stored)."
        )
    
    # --- 2. VALIDATE ALL FILES (Memory-Safe Pass) ---
    for file in files:
        if not file.content_type.startswith("image/"):
            logger.warning(f"Invalid file type uploaded by user {subscription.user_id}: {file.content_type}")
            raise APIException(status_code=400, msg=f"Invalid format for {file.filename}. Images only.")
        
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"File size exceeded for {file.filename} by user {subscription.user_id}")
            raise APIException(status_code=400, msg=f"File '{file.filename}' exceeds the 1MB size limit.")

    # --- 3. SAVE TO DISK & DATABASE ---
    uploaded_items_info = []
    
    try:
        for idx, file in enumerate(files):
            # Safe extension extraction
            ext = os.path.splitext(file.filename)[1] 
            filename = f"{uuid.uuid4().hex}{ext}"
            path = os.path.join(UPLOAD_DIR, filename)
            
            # Offload the blocking write operation to a background thread
            await run_in_threadpool(write_file_to_disk, file, path)
            
            item_label = label if len(files) == 1 else f"{label} - {idx + 1}"
            
            new_item = models.ClosetItem(
                user_id=subscription.user_id,
                file_path=path,
                label=item_label,
                category=category.value,
                wear_category=wear_category.value
            )
            db.add(new_item)
            db.flush()  
            
            uploaded_items_info.append({
                "closet_id": new_item.id,
                "label": item_label
            })
            
        db.commit() 
        logger.info(f"Successfully uploaded {len(uploaded_items_info)} closet items for user {subscription.user_id}")
        
        return StandardResponse(
            status=True,
            msg=f"{len(uploaded_items_info)} garment(s) uploaded to closet successfully",
            data={
                "user_id": subscription.user_id,
                "category": category.value,
                "wear_category": wear_category.value,
                "items": uploaded_items_info
            }
        )
        
    except Exception as e:
        logger.error(f"Error during batch closet upload for user {subscription.user_id}: {str(e)}")
        db.rollback() 
        raise APIException(status_code=500, msg="Internal server error during file upload.")
    

@router.get("/", response_model=StandardResponse)
def get_closet_items(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Retrieving closet items for user ID: {current_user.id}")
    
    try:
        items = db.query(models.ClosetItem).filter(models.ClosetItem.user_id == current_user.id).all()
        
        base_url = settings.BACKEND_URL.rstrip("/")
        
        results = []
        for item in items:
            clean_path = item.file_path.replace("\\", "/")
            if not clean_path.startswith("/"):
                clean_path = "/" + clean_path
            
            full_url = f"{base_url}{clean_path}"
            
            # Unpack Enum safely
            wear_cat_val = item.wear_category.value if hasattr(item.wear_category, 'value') else item.wear_category
            
            results.append({
                "closet_id": item.id,
                "label": item.label,
                "image_url": full_url,
                "category": item.category if item.category else "tops", 
                "wear_category": wear_cat_val or "mens", # EXPOSED IN GET API
                "user_id": current_user.id,
                "username": current_user.username,
                "email": current_user.email
            })
                
        return StandardResponse(
            status=True,
            msg="Closet items retrieved successfully",
            data=results
        )
        
    except APIException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving closet items for user {current_user.id}: {str(e)}")
        raise APIException(status_code=500, msg="Failed to retrieve closet items.")
    
    
    
    
@router.delete("/{closet_id}", response_model=StandardResponse)
def delete_closet_item(
    closet_id: int = Path(..., description="The ID of the closet item to delete"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"User ID: {current_user.id} attempting to delete closet item ID: {closet_id}")
    
    try:
        # 1. Query the item and ensure it belongs to the authenticated user
        item = db.query(models.ClosetItem).filter(
            models.ClosetItem.id == closet_id,
            models.ClosetItem.user_id == current_user.id
        ).first()
        
        if not item:
            logger.warning(f"Delete failed: Closet item {closet_id} not found for user {current_user.id}")
            raise APIException(status_code=404, msg="Closet item not found or you do not have permission to delete it.")
        
        # 2. Delete the physical file from the server storage
        if item.file_path and os.path.exists(item.file_path):
            try:
                os.remove(item.file_path)
                logger.info(f"Successfully deleted physical file: {item.file_path}")
            except Exception as e:
                # Log the file error, but don't stop the DB deletion
                logger.error(f"Failed to delete physical file {item.file_path}: {str(e)}")
        
        # 3. Delete the record from the database
        db.delete(item)
        db.commit()
        
        logger.info(f"Successfully deleted closet item ID: {closet_id} for user {current_user.id}")
        
        return StandardResponse(
            status=True,
            msg="Closet item deleted successfully.",
            data=None
        )
            
    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting closet item {closet_id} for user {current_user.id}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error. Failed to delete closet item.")
    
