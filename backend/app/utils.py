import os
import uuid
import shutil
import httpx
import logging
import aiofiles
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from . import models
from fastapi import UploadFile

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Define the directory where uploaded files will be stored permanently
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static_uploads")

# Ensure the upload directory exists on the Linux/Windows server filesystem
os.makedirs(UPLOAD_DIR, exist_ok=True)



# ==============================================================================
# FREE USER SUBSCRIPTION CHECKER
# ==============================================================================
def check_is_free_user(db: Session, user_id: int) -> bool:
    """
    Determines whether a user is on a Free tier plan.
    Returns True if user has no active plan, price is 0, or plan_name is 'Free'.
    """
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == user_id,
        models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
    ).first()

    if not sub or not sub.plan_snapshot:
        return True

    snapshot = sub.plan_snapshot
    plan_name = str(snapshot.get("plan_name", "")).strip().lower()
    price = float(snapshot.get("price", 0.0))

    return price == 0.0 or "free" in plan_name


def save_upload_file(upload_file: UploadFile) -> str:
    """
    Safely saves an uploaded file to the disk using streams to optimize memory usage.
    Appends a unique UUID to prevent file name collisions.
    Returns the newly generated unique filename.
    """
    original_name = upload_file.filename
    ext = os.path.splitext(original_name)[1]
    
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)
    finally:
        upload_file.file.close()
        
    return unique_filename


async def download_and_save_remote_image(remote_url: str, upload_dir: str = UPLOAD_DIR) -> str:
    """Downloads remote CDN assets (Fashn.ai) and saves them locally."""
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(remote_url)
            response.raise_for_status()

            ext = ".png"
            remote_lower = remote_url.lower()
            if ".jpg" in remote_lower or ".jpeg" in remote_lower:
                ext = ".jpg"
            elif ".mp4" in remote_lower:
                ext = ".mp4"

            unique_filename = f"gen_{uuid.uuid4().hex}{ext}"
            file_path = os.path.join(upload_dir, unique_filename)

            with open(file_path, "wb") as f:
                f.write(response.content)

            return unique_filename

    except Exception as e:
        logging.logger.error(f"Failed to download remote asset from {remote_url}: {str(e)}")
        raise e

