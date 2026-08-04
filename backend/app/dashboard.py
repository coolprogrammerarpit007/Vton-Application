import logging
import json
import httpx
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .auth import get_current_user
from .schemas import StandardResponse
from .exceptions import APIException
from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

FASHN_API_KEY = settings.FASHN_API_KEY
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
# 1. UNIFIED DASHBOARD API ENDPOINT (COMPLETED JOBS ONLY)
# ==============================================================================
@router.get("", response_model=StandardResponse)
async def get_dashboard_data(
    category_filter: Optional[str] = Query("all", alias="filter"),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        fashn_credits = await fetch_fashn_credits()
        credits_left = fashn_credits.get("total", 0)
        credits_max = fashn_credits.get("subscription", 100) or 100
        credits_low_warning = credits_left < 15

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_created = 0
        generations_list = []

        # A. Process Try-On & 360 Jobs (Lightweight Query)
        if category_filter in ["all", "tryon", "360"]:
            tryon_jobs = db.query(
                models.TryOnJob.id, models.TryOnJob.category, models.TryOnJob.garment_image_url, 
                models.TryOnJob.result_image_urls, models.TryOnJob.created_at
            ).filter(
                models.TryOnJob.user_id == current_user.id,
                models.TryOnJob.status == models.JobStatus.COMPLETED
            ).all()

            for job in tryon_jobs:
                created_dt = job.created_at.replace(tzinfo=None) if job.created_at else datetime.min
                if created_dt >= today_start:
                    today_created += 1

                raw_urls = job.result_image_urls
                if isinstance(raw_urls, str):
                    try: raw_urls = json.loads(raw_urls)
                    except json.JSONDecodeError: raw_urls = []

                primary_url = job.garment_image_url
                if isinstance(raw_urls, list) and raw_urls:
                    primary_url = raw_urls[0]
                elif isinstance(raw_urls, dict) and raw_urls:
                    primary_url = raw_urls.get("front") or next(iter(raw_urls.values()), job.garment_image_url)

                category_name = job.category.value if hasattr(job.category, 'value') else str(job.category)
                badge_label = "360 View" if isinstance(raw_urls, dict) else "Virtual Try-On"

                generations_list.append({
                    "job_id": job.id, "title": f"{category_name.title()} Try-On", "badge": badge_label,
                    "type": "tryon", "status": "completed", "result_url": primary_url, 
                    "created_at": job.created_at.isoformat() if job.created_at else "", "_sort_dt": created_dt
                })

        # B. Process Outfit Jobs (Lightweight Query)
        if category_filter in ["all", "outfit"]:
            outfit_jobs = db.query(
                models.OutfitJob.id, models.OutfitJob.styling_prompt, models.OutfitJob.result_image_url, 
                models.OutfitJob.person_image_url, models.OutfitJob.created_at
            ).filter(
                models.OutfitJob.user_id == current_user.id,
                models.OutfitJob.status == models.JobStatus.COMPLETED
            ).all()

            for job in outfit_jobs:
                created_dt = job.created_at.replace(tzinfo=None) if getattr(job, 'created_at', None) else datetime.min
                if created_dt >= today_start:
                    today_created += 1

                title = job.styling_prompt if (job.styling_prompt and job.styling_prompt.strip()) else "Outfit Builder Generation"
                primary_url = job.result_image_url or job.person_image_url

                generations_list.append({
                    "job_id": job.id, "title": title, "badge": "Outfit Builder", "type": "outfit",
                    "status": "completed", "result_url": primary_url, 
                    "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else "", "_sort_dt": created_dt 
                })

        # C. Process Studio Jobs (Lightweight Query)
        if category_filter in ["all", "models"]:
            studio_jobs = db.query(
                models.StudioJob.id, models.StudioJob.job_type, models.StudioJob.input_data, 
                models.StudioJob.result_urls, models.StudioJob.created_at
            ).filter(
                models.StudioJob.user_id == current_user.id,
                models.StudioJob.status == models.JobStatus.COMPLETED
            ).all()

            for job in studio_jobs:
                created_dt = job.created_at.replace(tzinfo=None) if job.created_at else datetime.min
                if created_dt >= today_start:
                    today_created += 1

                urls = job.result_urls if isinstance(job.result_urls, list) else (json.loads(job.result_urls) if job.result_urls else [])
                primary_url = urls[0] if urls else ""
                
                input_dict = job.input_data if isinstance(job.input_data, dict) else {}
                prompt_text = input_dict.get("prompt", "AI Creative Studio Model")
                job_type_str = job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type)

                generations_list.append({
                    "job_id": job.id, "title": prompt_text, "badge": "AI Model", "type": job_type_str,
                    "status": "completed", "result_url": primary_url, 
                    "created_at": job.created_at.isoformat() if job.created_at else "", "_sort_dt": created_dt
                })

        if search and search.strip():
            search_lower = search.strip().lower()
            generations_list = [
                item for item in generations_list 
                if search_lower in item["title"].lower() or search_lower in item["badge"].lower()
            ]

        generations_list.sort(key=lambda x: (x["_sort_dt"], x["job_id"]), reverse=True)
        total_created = len(generations_list)

        for item in generations_list:
            item.pop("_sort_dt", None)

        paginated_generations = generations_list[skip : skip + limit]

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
                    "total": total_created,
                    "skip": skip,
                    "limit": limit,
                    "items": paginated_generations
                }
            }
        )

    except Exception as e:
        logger.error(f"Error fetching dashboard data: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg=f"Failed to load dashboard data.")

# ==============================================================================
# 2. UNIFIED GENERATION HISTORY API 
# ==============================================================================
@router.get("/studio-history", response_model=StandardResponse)
async def get_studio_history(
    model_type: Optional[str] = Query(None, description="Filter by model type: 'tryon', '360', 'outfit', or specific studio types"),
    media_filter: Optional[str] = Query("all", description="'all', 'images', or 'videos'"),
    status_filter: Optional[str] = Query("completed", description="'all', 'completed', or 'generating'"),
    search: Optional[str] = Query(None, description="Search by prompt or title"),
    skip: int = Query(0, ge=0),     
    limit: int = Query(20, ge=1),   
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        fashn_credits = await fetch_fashn_credits()
        remaining_credits = fashn_credits.get("total", 0)
        total_credits_given = fashn_credits.get("subscription", 100) or 100

        total_images = 0
        total_videos = 0
        completed_count = 0
        raw_generations_list = []

        # 3A. Process Studio Jobs (Lightweight Query)
        studio_jobs = db.query(
            models.StudioJob.id, models.StudioJob.job_type, models.StudioJob.status,
            models.StudioJob.input_data, models.StudioJob.result_urls, models.StudioJob.created_at
        ).filter(models.StudioJob.user_id == current_user.id, models.StudioJob.is_active == True).all()

        for job in studio_jobs:
            if job.status == models.JobStatus.COMPLETED: completed_count += 1
            
            is_video = job.job_type == models.StudioJobType.IMAGE_TO_VIDEO
            if is_video: total_videos += 1
            
            urls = job.result_urls if isinstance(job.result_urls, list) else (json.loads(job.result_urls) if job.result_urls else [])
            
            input_dict = job.input_data if isinstance(job.input_data, dict) else {}
            if not is_video:
                img_count = len(urls) if urls else input_dict.get("num_images", 1)
                total_images += img_count

            title = input_dict.get("prompt", "") or input_dict.get("bg_prompt", "Studio Generation")
            created_dt = job.created_at.replace(tzinfo=None) if getattr(job, 'created_at', None) else datetime.min

            raw_generations_list.append({
                "job_id": f"studio_{job.id}", "title": title,
                "model_type": job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type),
                "media_format": "video" if is_video else "image",
                "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
                "result_url": urls[0] if urls else "", "all_urls": urls,
                "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else "", "_sort_dt": created_dt
            })

        # 3B. Process Try-On & 360 Jobs (Lightweight Query)
        tryon_jobs = db.query(
            models.TryOnJob.id, models.TryOnJob.category, models.TryOnJob.status,
            models.TryOnJob.garment_image_url, models.TryOnJob.result_image_urls, models.TryOnJob.created_at
        ).filter(models.TryOnJob.user_id == current_user.id).all()

        for job in tryon_jobs:
            if job.status == models.JobStatus.COMPLETED: completed_count += 1
            
            raw_urls = job.result_image_urls
            if isinstance(raw_urls, str):
                try: raw_urls = json.loads(raw_urls)
                except json.JSONDecodeError: raw_urls = []
                
            all_urls_list = []
            primary_url = job.garment_image_url
            is_360 = False
            
            if isinstance(raw_urls, list) and raw_urls:
                primary_url = raw_urls[0]
                all_urls_list = raw_urls
            elif isinstance(raw_urls, dict) and raw_urls:
                is_360 = True
                primary_url = raw_urls.get("front") or next(iter(raw_urls.values()), job.garment_image_url)
                all_urls_list = list(raw_urls.values())
                
            total_images += len(all_urls_list) if all_urls_list else 1
            category_name = job.category.value if hasattr(job.category, 'value') else str(job.category)
            created_dt = job.created_at.replace(tzinfo=None) if getattr(job, 'created_at', None) else datetime.min

            raw_generations_list.append({
                "job_id": f"tryon_{job.id}", "title": f"{category_name.title()} Try-On",
                "model_type": "360" if is_360 else "tryon", "media_format": "image",
                "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
                "result_url": primary_url, "all_urls": all_urls_list,
                "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else "", "_sort_dt": created_dt
            })

        # 3C. Process Outfit Jobs (Lightweight Query)
        outfit_jobs = db.query(
            models.OutfitJob.id, models.OutfitJob.styling_prompt, models.OutfitJob.status,
            models.OutfitJob.result_image_url, models.OutfitJob.person_image_url, models.OutfitJob.created_at
        ).filter(models.OutfitJob.user_id == current_user.id).all()

        for job in outfit_jobs:
            if job.status == models.JobStatus.COMPLETED: completed_count += 1
            total_images += 1
            
            title = job.styling_prompt if (job.styling_prompt and job.styling_prompt.strip()) else "Outfit Builder"
            primary_url = job.result_image_url or job.person_image_url
            created_dt = job.created_at.replace(tzinfo=None) if getattr(job, 'created_at', None) else datetime.min

            raw_generations_list.append({
                "job_id": f"outfit_{job.id}", "title": title, "model_type": "outfit", "media_format": "image",
                "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
                "result_url": primary_url, "all_urls": [primary_url] if primary_url else [],
                "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else "", "_sort_dt": created_dt
            })

        total_jobs = len(studio_jobs) + len(tryon_jobs) + len(outfit_jobs)
        success_rate = round((completed_count / total_jobs * 100), 1) if total_jobs > 0 else 100.0

        # Filtering
        filtered_list = raw_generations_list
        if media_filter == "videos": filtered_list = [j for j in filtered_list if j["media_format"] == "video"]
        elif media_filter == "images": filtered_list = [j for j in filtered_list if j["media_format"] == "image"]

        if model_type and model_type != "all":
            filtered_list = [j for j in filtered_list if j["model_type"] == model_type]

        if status_filter == "completed":
            filtered_list = [j for j in filtered_list if j["status"] == "completed"]
        elif status_filter == "generating":
            filtered_list = [j for j in filtered_list if j["status"] in ["pending", "processing"]]

        if search and search.strip():
            search_lower = search.strip().lower()
            filtered_list = [item for item in filtered_list if search_lower in item["title"].lower() or search_lower in item["model_type"].lower()]

        # Sorting & Cleanup
        filtered_list.sort(key=lambda x: x["_sort_dt"], reverse=True)
        total_matching = len(filtered_list)
        for item in filtered_list: item.pop("_sort_dt", None)

        # APPLY PAGINATION (FIXED)
        paginated_items = filtered_list[skip : skip + limit]

        return StandardResponse(
            status=True,
            msg="Unified history retrieved successfully.",
            data={
                "header_stats": {
                    "images": f"{total_images:,}",
                    "videos": f"{total_videos:,}",
                    "total_credits_given": total_credits_given,
                    "remaining_credits": remaining_credits,
                    "success_rate": f"{success_rate}%"
                },
                "feed": {
                    "total_matching": total_matching,
                    "skip": skip,
                    "limit": limit, 
                    "items": paginated_items
                }
            }
        )

    except Exception as e:
        logger.error(f"Error fetching unified history: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to load history.")
    
# ==============================================================================
# 3. GET CREDITS 
# ==============================================================================
@router.get("/credits", response_model=StandardResponse)
async def get_user_credits(current_user: models.User = Depends(get_current_user)):
    try:
        plan_name = getattr(current_user, "plan_name", "Free Tier")
        
        # Reuse the existing helper function instead of repeating the httpx call
        fashn_credits = await fetch_fashn_credits()

        return StandardResponse(
            status=True,
            msg="Credits retrieved successfully.",
            data={
                "plan_name": plan_name,
                "total_credits_remaining": fashn_credits.get("total", 0),
                "fashn_breakdown": {
                    "subscription": fashn_credits.get("subscription", 0),
                    "on_demand": fashn_credits.get("on_demand", 0)
                }
            }
        )

    except Exception as e:
        logger.error(f"Error fetching credits: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to fetch credit information.")