import os
import uuid
import logging
from logging.handlers import TimedRotatingFileHandler
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from . import models
from .database import get_db
from .auth import get_current_user

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

@router.post("/upload")
async def upload_closet_item(
    category: str = Form(...),
    label: str = Form("My Garment"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Closet upload attempt by user ID: {current_user.id} | Filename: {file.filename}")
    
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
        
        logger.info(f"Closet item saved successfully for user {current_user.id} | Item ID: {new_item.id}")
        return {"message": "Saved!", "id": new_item.id}
        
    except Exception as e:
        logger.error(f"Error during closet upload for user {current_user.id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
    
    
@router.get("/")
def get_closet_items(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Retrieving closet items for user ID: {current_user.id}")
    items = db.query(models.ClosetItem).filter(models.ClosetItem.user_id == current_user.id).all()
    
    # Transform the local file path into a URL the frontend can access
    # Assuming your mount point is /static_uploads
    results = []
    for item in items:
        # Convert absolute path to a URL-friendly path
        url_path = item.file_path.replace("static_uploads", "/static_uploads")
        results.append({
            "id": item.id,
            "label": item.label,
            "image_url": f"{url_path}" 
        })
    return results