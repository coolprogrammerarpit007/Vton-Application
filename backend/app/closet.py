from fastapi import APIRouter, Depends, UploadFile, File, Form, Request
from sqlalchemy.orm import Session
from typing import List

import os
import uuid
import logging
from logging.handlers import TimedRotatingFileHandler

from . import models
from .database import get_db
from .schemas import StandardResponse
from .auth import get_current_user
from app.exceptions import APIException

# --- Logging Configuration (Daily Rotating) ---
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("closet_logger")
logger.setLevel(logging.INFO)

if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    file_handler = TimedRotatingFileHandler(
        filename="logs/closet.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

router = APIRouter(prefix="/api/closet", tags=["Closet"])

UPLOAD_DIR = "static_uploads/closet"
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 Megabytes

@router.post("/upload", response_model=StandardResponse)
async def upload_closet_item(
    category: str = Form("tops"),
    wear_category: str = Form(...),  # REQUIRED: "mens", "women", or "kids"
    label: str = Form("My Garment"),
    files: List[UploadFile] = File(...),  # ACCEPTS MULTIPLE FILES
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Batch closet upload attempt by user ID: {current_user.id} | Items: {len(files)}")
    
    # --- 1. VALIDATE ENUMS ---
    try:
        validated_category = models.GarmentCategory(category.lower())
    except ValueError:
        raise APIException(status_code=400, msg=f"Invalid category: {category}. Allowed: tops, bottoms, one-pieces.")
        
    try:
        validated_wear = models.WearCategory(wear_category.lower())
    except ValueError:
        raise APIException(status_code=400, msg=f"Invalid wear_category: {wear_category}. Allowed: mens, women, kids.")

    # --- 2. VALIDATE ALL FILES (Two-Pass System for Safety) ---
    valid_files_data = []
    
    for file in files:
        if not file.content_type.startswith("image/"):
            logger.warning(f"Invalid file type uploaded by user {current_user.id}: {file.content_type}")
            raise APIException(status_code=400, msg=f"Invalid format for {file.filename}. Images only.")
        
        file_content = await file.read()
        if len(file_content) > MAX_FILE_SIZE:
            logger.warning(f"File size exceeded for {file.filename} by user {current_user.id}")
            raise APIException(status_code=400, msg=f"File '{file.filename}' exceeds the 2MB size limit.")
            
        valid_files_data.append((file.filename, file_content))

    # --- 3. SAVE TO DISK & DATABASE ---
    uploaded_items_info = []
    try:
        for idx, (original_name, content) in enumerate(valid_files_data):
            # Save file locally
            file_ext = original_name.split(".")[-1]
            filename = f"{uuid.uuid4().hex}.{file_ext}"
            path = os.path.join(UPLOAD_DIR, filename)
            
            with open(path, "wb") as buffer:
                buffer.write(content)
            
            # Dynamic Labeling (e.g. "My Garment - 1" if multiple files are uploaded)
            item_label = label if len(valid_files_data) == 1 else f"{label} - {idx + 1}"
            
            # Save to Database
            new_item = models.ClosetItem(
                user_id=current_user.id,
                file_path=path,
                label=item_label,
                category=validated_category.value,
                wear_category=validated_wear.value
            )
            db.add(new_item)
            db.flush()  # Generates the ID without committing the transaction yet
            
            uploaded_items_info.append({
                "closet_id": new_item.id,
                "label": item_label
            })
            
        db.commit() # Commit all entries at once
        logger.info(f"Successfully uploaded {len(uploaded_items_info)} closet items for user {current_user.id}")
        
        return StandardResponse(
            status=True,
            msg=f"{len(uploaded_items_info)} garment(s) uploaded to closet successfully",
            data={
                "user_id": current_user.id,
                "category": validated_category.value,
                "wear_category": validated_wear.value,
                "items": uploaded_items_info
            }
        )
        
    except Exception as e:
        logger.error(f"Error during batch closet upload for user {current_user.id}: {str(e)}")
        db.rollback() 
        raise APIException(status_code=500, msg="Internal server error during file upload.")
    

@router.get("/", response_model=StandardResponse)
def get_closet_items(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Retrieving closet items for user ID: {current_user.id}")
    
    try:
        items = db.query(models.ClosetItem).filter(models.ClosetItem.user_id == current_user.id).all()
        base_url = str(request.base_url).rstrip("/")
        
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