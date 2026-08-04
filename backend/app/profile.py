import os
import uuid
import json
import logging
import httpx
import shutil
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .auth import get_current_user
from .schemas import StandardResponse
from .exceptions import APIException
from .config import settings  

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["Profile"])

FASHN_API_KEY = settings.FASHN_API_KEY  
FASHN_CREDITS_URL = "https://api.fashn.ai/v1/credits"

AVATAR_UPLOAD_DIR = "static_uploads/avatars"
os.makedirs(AVATAR_UPLOAD_DIR, exist_ok=True)

# Helper to write avatar files without blocking the event loop
def write_avatar_to_disk(upload_file: UploadFile, dest_path: str):
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

# Helper to fetch real-time FASHN credits
async def fetch_fashn_credits() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"Authorization": f"Bearer {FASHN_API_KEY}"}
            response = await client.get(FASHN_CREDITS_URL, headers=headers)
            if response.status_code == 200:
                return response.json().get("credits", {"total": 0, "subscription": 0, "on_demand": 0})
    except Exception as e:
        logger.error(f"Failed to fetch FASHN credits: {str(e)}")
    return {"total": 0, "subscription": 0, "on_demand": 0}

# ==============================================================================
# 1. GET PROFILE DETAILS & STATS
# ==============================================================================
@router.get("", response_model=StandardResponse)
async def get_user_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # A. Fetch live credits balance from FASHN
        fashn_credits = await fetch_fashn_credits()
        credits_left = fashn_credits.get("total", 0)

        # B. Calculate totals efficiently by querying ONLY required columns
        total_images = 0
        total_videos = 0

        # Extract only the result URLs for TryOn (Memory Safe)
        tryon_results = db.query(models.TryOnJob.result_image_urls).filter(
            models.TryOnJob.user_id == current_user.id
        ).all()
        
        for (raw_urls,) in tryon_results:
            if isinstance(raw_urls, str):
                try: 
                    raw_urls = json.loads(raw_urls)
                except json.JSONDecodeError: 
                    raw_urls = []
                
            if isinstance(raw_urls, (dict, list)):
                total_images += len(raw_urls)
            elif raw_urls:
                total_images += 1

        # Use native SQL count for Outfit Builder since it's 1-to-1
        total_images += db.query(models.OutfitJob.id).filter(
            models.OutfitJob.user_id == current_user.id
        ).count()

        # Extract only job_type and result_urls for Studio (Memory Safe)
        studio_results = db.query(models.StudioJob.job_type, models.StudioJob.result_urls).filter(
            models.StudioJob.user_id == current_user.id
        ).all()

        for job_type, raw_urls in studio_results:
            if job_type == models.StudioJobType.IMAGE_TO_VIDEO:
                total_videos += 1
            else:
                if isinstance(raw_urls, str):
                    try: 
                        raw_urls = json.loads(raw_urls)
                    except json.JSONDecodeError: 
                        raw_urls = []
                    
                if isinstance(raw_urls, (dict, list)):
                    total_images += len(raw_urls)
                elif raw_urls:
                    total_images += 1

        # C. Format Name and Avatar URL dynamically via settings
        display_name = getattr(current_user, 'full_name', None) or current_user.username
        plan_badge = getattr(current_user, 'plan_name', 'PRO')
        
        avatar_url = getattr(current_user, 'avatar_url', None)
        if avatar_url and not avatar_url.startswith("http"):
            base_url = settings.BACKEND_URL.rstrip("/")
            clean_path = avatar_url.replace("\\", "/")
            if not clean_path.startswith("/"):
                clean_path = "/" + clean_path
            avatar_url = f"{base_url}{clean_path}"

        # D. Assemble response matching the UI structure
        return StandardResponse(
            status=True,
            msg="User profile retrieved successfully.",
            data={
                "user_info": {
                    "id": current_user.id,
                    "full_name": display_name,
                    "email": current_user.email,
                    "membership_badge": f"{plan_badge.title()} Member",
                    "avatar_url": avatar_url or ""
                },
                "stats_cards": {
                    "credits_left": f"{credits_left:,}",
                    "images_generated": f"{total_images:,}",
                    "videos_created": f"{total_videos:,}",
                    "current_plan": plan_badge.upper()
                }
            }
        )

    except Exception as e:
        logger.error(f"Error retrieving user profile: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to load profile details.")

# ==============================================================================
# 2. UPLOAD / UPDATE PROFILE AVATAR PHOTO
# ==============================================================================
@router.post("/avatar", response_model=StandardResponse)
async def upload_avatar_photo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not file.content_type.startswith("image/"):
        raise APIException(status_code=400, msg="Invalid file format. Please upload an image.")

    try:
        # Safely extract extension
        ext = os.path.splitext(file.filename)[1]
        filename = f"avatar_user_{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
        file_path = os.path.join(AVATAR_UPLOAD_DIR, filename)

        # Offload file writing to a background thread to keep FastAPI non-blocking
        await run_in_threadpool(write_avatar_to_disk, file, file_path)

        current_user.avatar_url = file_path
        db.commit()
        db.refresh(current_user)

        # Generate absolute URL dynamically from config
        base_url = settings.BACKEND_URL.rstrip("/")
        clean_path = file_path.replace("\\", "/")
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path
        full_avatar_url = f"{base_url}{clean_path}"

        return StandardResponse(
            status=True,
            msg="Profile avatar updated successfully.",
            data={"avatar_url": full_avatar_url}
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error uploading avatar: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to update avatar photo.")