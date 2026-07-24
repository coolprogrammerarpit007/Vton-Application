import logging
import json
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .auth import get_current_user
from .schemas import StandardResponse
from .exceptions import APIException


from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])
# router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

FASHN_API_KEY = settings.FASHN_API_KEY  # Replace with your actual FASHN API key or load from env
FASHN_CREDITS_URL = "https://api.fashn.ai/v1/credits"

# ==============================================================================
# HELPER: Live FASHN API Credit Fetcher
# ==============================================================================
async def fetch_fashn_credits() -> Dict[str, Any]:
    """Fetches real-time credit balance directly from FASHN API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"Authorization": f"Bearer {FASHN_API_KEY}"}
            response = await client.get(FASHN_CREDITS_URL, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return data.get("credits", {"total": 0, "subscription": 0, "on_demand": 0})
    except Exception as e:
        logger.error(f"Failed to fetch live FASHN credits: {str(e)}")
    
    return {"total": 0, "subscription": 0, "on_demand": 0}


# ==============================================================================
# UNIFIED DASHBOARD API ENDPOINT (COMPLETED JOBS ONLY)
# ==============================================================================
@router.get("", response_model=StandardResponse)
async def get_dashboard_data(
    category_filter: Optional[str] = Query(
        "all", 
        alias="filter",
        description="Tab filters: 'all', 'tryon', '360', 'outfit', 'models'"
    ),
    search: Optional[str] = Query(
        None, 
        description="Search term to filter generations by title or prompt"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # ----------------------------------------------------------------------
        # 1. FETCH LIVE FASHN CREDITS
        # ----------------------------------------------------------------------
        fashn_credits = await fetch_fashn_credits()
        credits_left = fashn_credits.get("total", 0)
        credits_max = fashn_credits.get("subscription", 100) or 100
        credits_low_warning = credits_left < 15

        # ----------------------------------------------------------------------
        # 2. QUERY COMPLETED JOBS ONLY
        # ----------------------------------------------------------------------
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # Strictly query jobs where status == COMPLETED
        tryon_jobs = db.query(models.TryOnJob).filter(
            models.TryOnJob.user_id == current_user.id,
            models.TryOnJob.status == models.JobStatus.COMPLETED
        ).all()

        outfit_jobs = db.query(models.OutfitJob).filter(
            models.OutfitJob.user_id == current_user.id,
            models.OutfitJob.status == models.JobStatus.COMPLETED
        ).all()

        studio_jobs = db.query(models.StudioJob).filter(
            models.StudioJob.user_id == current_user.id,
            models.StudioJob.status == models.JobStatus.COMPLETED
        ).all()

        # Overall Stats
        total_created = len(tryon_jobs) + len(outfit_jobs) + len(studio_jobs)
        today_created = 0

        def is_created_today(created_at_dt):
            if not created_at_dt:
                return False
            if created_at_dt.tzinfo is not None:
                created_at_dt = created_at_dt.replace(tzinfo=None)
            return created_at_dt >= today_start

        for j in tryon_jobs:
            if is_created_today(j.created_at):
                today_created += 1

        for j in studio_jobs:
            if is_created_today(j.created_at):
                today_created += 1

        # ----------------------------------------------------------------------
        # 3. BUILD RECENT GENERATIONS FEED (SINGLE URL ONLY)
        # ----------------------------------------------------------------------
        generations_list = []

        # A. Process Try-On & 360 Jobs
        if category_filter in ["all", "tryon", "360"]:
            for job in tryon_jobs:
                urls = []
                if job.result_image_urls:
                    urls = job.result_image_urls if isinstance(job.result_image_urls, list) else json.loads(job.result_image_urls)

                # Single URL extraction logic
                primary_url = urls[0] if (urls and len(urls) > 0) else job.garment_image_url
                category_name = job.category.value if hasattr(job.category, 'value') else str(job.category)

                created_dt = job.created_at.replace(tzinfo=None) if job.created_at else datetime.min

                generations_list.append({
                    "job_id": job.id,
                    "title": f"{category_name.title()} Try-On",
                    "badge": "Virtual Try-On",
                    "type": "tryon",
                    "status": "completed",
                    "result_url": primary_url,  # Single URL string
                    "created_at": job.created_at.isoformat() if job.created_at else "",
                    "_sort_dt": created_dt
                })

        # B. Process Outfit Jobs
        if category_filter in ["all", "outfit"]:
            for job in outfit_jobs:
                title = job.styling_prompt if (job.styling_prompt and job.styling_prompt.strip()) else "Outfit Builder Generation"
                
                # Outfit jobs return a single image URL
                primary_url = job.result_image_url if job.result_image_url else job.person_image_url

                # SAFELY EXTRACT THE NEW TIMESTAMP FOR SORTING
                created_dt = job.created_at.replace(tzinfo=None) if getattr(job, 'created_at', None) else datetime.min

                generations_list.append({
                    "job_id": job.id,
                    "title": title,
                    "badge": "Outfit Builder",
                    "type": "outfit",
                    "status": "completed",
                    "result_url": primary_url, 
                    "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else "",
                    "_sort_dt": created_dt # Now this will sort correctly!
                })

        # C. Process Studio Jobs
        if category_filter in ["all", "models"]:
            for job in studio_jobs:
                urls = []
                if job.result_urls:
                    urls = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)

                primary_url = urls[0] if (urls and len(urls) > 0) else ""
                prompt_text = job.input_data.get("prompt", "AI Creative Studio Model") if isinstance(job.input_data, dict) else "AI Studio Model"
                job_type_str = job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type)

                created_dt = job.created_at.replace(tzinfo=None) if job.created_at else datetime.min

                generations_list.append({
                    "job_id": job.id,
                    "title": prompt_text,
                    "badge": "AI Model",
                    "type": job_type_str,
                    "status": "completed",
                    "result_url": primary_url,  # Single URL string
                    "created_at": job.created_at.isoformat() if job.created_at else "",
                    "_sort_dt": created_dt
                })

        # ----------------------------------------------------------------------
        # 4. SEARCH, SORT, AND PAGINATE
        # ----------------------------------------------------------------------
        if search and search.strip():
            search_lower = search.strip().lower()
            generations_list = [
                item for item in generations_list 
                if search_lower in item["title"].lower() or search_lower in item["badge"].lower()
            ]

        # Multi-key sorting: Sort by timestamp first, then by job_id
        generations_list.sort(key=lambda x: (x["_sort_dt"], x["job_id"]), reverse=True)

        # Cleanup internal sorting key before returning response
        for item in generations_list:
            item.pop("_sort_dt", None)

        paginated_generations = generations_list[skip : skip + limit]

        # ----------------------------------------------------------------------
        # 5. RESPONSE PAYLOAD
        # ----------------------------------------------------------------------
        return StandardResponse(
            status=True,
            msg="Dashboard data for completed jobs retrieved successfully.",
            data={
                "user": {
                    "username": current_user.username,
                    "email": current_user.email,
                    "plan": "Pro Member Plan"
                },
                "stats": {
                    "total_created": total_created,
                    "created_today": f"+{today_created} Today",
                    "credits_left": credits_left,
                    "credits_max": credits_max,
                    "credits_warning": credits_low_warning,
                    "success_rate": "100.0%",
                    "quality_badge": "High Quality"
                },
                "recent_generations": {
                    "total": len(generations_list),
                    "skip": skip,
                    "limit": limit,
                    "items": paginated_generations
                }
            }
        )

    except Exception as e:
        logger.error(f"Error fetching dashboard data: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg=f"Failed to load dashboard data: {str(e)}")
    
    
 # ==============================================================================
# 2. STUDIO MODEL GENERATION HISTORY API (GET /api/dashboard/studio-history)
# ==============================================================================
@router.get("/studio-history", response_model=StandardResponse)
async def get_studio_history(
    model_type: Optional[models.StudioJobType] = Query(None, description="Filter by specific Studio model type"),
    media_filter: Optional[str] = Query("all", description="'all', 'images', or 'videos'"),
    status_filter: Optional[str] = Query("all", description="'all', 'completed', or 'generating'"),
    search: Optional[str] = Query(None, description="Search by prompt or title"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # 1. Fetch Live Credits
        fashn_credits = await fetch_fashn_credits()
        remaining_credits = fashn_credits.get("total", 0)
        total_credits_given = fashn_credits.get("subscription", 100) or 100

        # 2. Query ALL Studio Jobs for this user
        all_user_jobs = db.query(models.StudioJob).filter(
            models.StudioJob.user_id == current_user.id
        ).all()

        total_images = 0
        total_videos = 0
        completed_count = 0

        # 3. Calculate Global Stats (Images, Videos, Success Rate)
        for job in all_user_jobs:
            if job.status == models.JobStatus.COMPLETED:
                completed_count += 1

            if job.job_type == models.StudioJobType.IMAGE_TO_VIDEO:
                total_videos += 1
            else:
                urls = []
                if job.result_urls:
                    urls = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)
                
                img_count = len(urls) if urls else (job.input_data.get("num_images", 1) if isinstance(job.input_data, dict) else 1)
                total_images += img_count

        total_jobs = len(all_user_jobs)
        success_rate = round((completed_count / total_jobs * 100), 1) if total_jobs > 0 else 100.0

        # 4. Filter the feed based on the UI Tabs requested
        filtered_jobs = all_user_jobs

        if media_filter == "videos":
            filtered_jobs = [j for j in filtered_jobs if j.job_type == models.StudioJobType.IMAGE_TO_VIDEO]
        elif media_filter == "images":
            filtered_jobs = [j for j in filtered_jobs if j.job_type != models.StudioJobType.IMAGE_TO_VIDEO]

        if model_type:
            filtered_jobs = [j for j in filtered_jobs if j.job_type == model_type]

        if status_filter == "completed":
            filtered_jobs = [j for j in filtered_jobs if j.status == models.JobStatus.COMPLETED]
        elif status_filter == "generating":
            filtered_jobs = [j for j in filtered_jobs if j.status in [models.JobStatus.PENDING, models.JobStatus.PROCESSING]]

        # 5. Format the Feed Output
        generations_list = []
        for job in filtered_jobs:
            urls = []
            if job.result_urls:
                urls = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)

            primary_url = urls[0] if urls else ""
            input_dict = job.input_data if isinstance(job.input_data, dict) else {}
            title = input_dict.get("prompt", "") or input_dict.get("bg_prompt", "Studio Generation")
            is_video = job.job_type == models.StudioJobType.IMAGE_TO_VIDEO
            
            created_dt = job.created_at.replace(tzinfo=None) if getattr(job, 'created_at', None) else datetime.min

            generations_list.append({
                "job_id": job.id,
                "title": title,
                "model_type": job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type),
                "media_format": "video" if is_video else "image",
                "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
                "result_url": primary_url,
                "all_urls": urls,
                "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else "",
                "_sort_dt": created_dt
            })

        # Apply Search
        if search and search.strip():
            search_lower = search.strip().lower()
            generations_list = [
                item for item in generations_list 
                if search_lower in item["title"].lower() or search_lower in item["model_type"].lower()
            ]

        # Sort newest first & Paginate
        generations_list.sort(key=lambda x: x["_sort_dt"], reverse=True)
        for item in generations_list:
            item.pop("_sort_dt", None)
            
        paginated = generations_list[skip : skip + limit]

        # 6. Construct Final Response Payload
        return StandardResponse(
            status=True,
            msg="Studio history retrieved successfully.",
            data={
                "header_stats": {
                    "images": f"{total_images:,}",
                    "videos": f"{total_videos:,}",
                    "total_credits_given": total_credits_given,
                    "remaining_credits": remaining_credits,
                    "success_rate": f"{success_rate}%"
                },
                "feed": {
                    "total_matching": len(generations_list),
                    "skip": skip,
                    "limit": limit,
                    "items": paginated
                }
            }
        )

    except Exception as e:
        logger.error(f"Error fetching studio history: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg=f"Failed to load history: {str(e)}")