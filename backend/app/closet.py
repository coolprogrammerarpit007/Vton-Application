from fastapi import APIRouter, Depends, UploadFile, File, Form,Request
from sqlalchemy.orm import Session


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
    
    # Console Handler for real-time monitoring
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Daily File Handler
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

@router.post("/upload", response_model=StandardResponse)
async def upload_closet_item(
    category: str = Form(...),
    label: str = Form("My Garment"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Closet upload attempt by user ID: {current_user.id} | Filename: {file.filename}")
    
    # --- VALIDATION: Ensure the file is actually an image ---
    if not file.content_type.startswith("image/"):
        logger.warning(f"Invalid file type uploaded by user {current_user.id}: {file.content_type}")
        raise APIException(status_code=400, msg="Invalid file format. Please upload an image.")
    
    try:
        # 1. Save file locally
        file_ext = file.filename.split(".")[-1]
        filename = f"{uuid.uuid4().hex}.{file_ext}"
        path = os.path.join(UPLOAD_DIR, filename)
        
        with open(path, "wb") as buffer:
            buffer.write(await file.read())
        
        # 2. Save to Database
        new_item = models.ClosetItem(
            user_id=current_user.id,
            file_path=path,
            label=label
        )
        db.add(new_item)
        db.commit()
        db.refresh(new_item)
        
        logger.info(f"Closet item saved successfully for user {current_user.id} | Item ID: {new_item.id}")
        
        # --- SUCCESS RESPONSE ---
        return StandardResponse(
            status=True,
            msg="Garment uploaded to closet successfully",
            data={
                "closet_id": new_item.id,
                "user_id": current_user.id
            }
        )
        
    except APIException:
        raise
    except Exception as e:
        logger.error(f"Error during closet upload for user {current_user.id}: {str(e)}")
        db.rollback() # Rollback in case of DB failure
        raise APIException(status_code=500, msg="Internal server error during file upload.")
    

@router.get("/", response_model=StandardResponse)
def get_closet_items(
    request: Request, # Inject the Request object to dynamically read your server's domain
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Retrieving closet items for user ID: {current_user.id}")
    
    try:
        items = db.query(models.ClosetItem).filter(models.ClosetItem.user_id == current_user.id).all()
        
        # Grab the current server domain dynamically (e.g., http://127.0.0.1:8000)
        base_url = str(request.base_url).rstrip("/")
        
        results = []
        for item in items:
            # 1. Convert Windows backslashes to Web forward slashes
            clean_path = item.file_path.replace("\\", "/")
            
            # 2. Ensure the path starts with a single slash
            if not clean_path.startswith("/"):
                clean_path = "/" + clean_path
            
            # 3. Combine the domain and the clean path into a full absolute URL
            full_url = f"{base_url}{clean_path}"
            
            results.append({
                "closet_id": item.id,
                "label": item.label,
                "image_url": full_url,
                
                # Appended user details as requested
                "user_id": current_user.id,
                "username": current_user.username,
                "email": current_user.email
            })
            
        # --- SUCCESS RESPONSE ---
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