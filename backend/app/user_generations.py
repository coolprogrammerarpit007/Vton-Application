from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional
import json

from . import models
from .database import get_db
from .auth import get_current_user
from .schemas import StandardResponse
from .exceptions import APIException

router = APIRouter(tags=["Generations"])

# ==============================================================================
# GET USER GENERATIONS (Try-On, Outfit Builder, 360 View)
# ==============================================================================
@router.get("/api/my-generations", response_model=StandardResponse)
async def get_user_generations(
    generation_type: Optional[models.MasterModuleType] = Query(
        None, 
        description="Filter by type: 'tryon', 'three-sixty', or 'outfit'. Omit to fetch all."
    ),
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Max number of records to return"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        combined_results = []
        
        # ---------------------------------------------------------
        # 1. FETCH TRY-ON & 360 JOBS (Stored in TryOnJob Table)
        # ---------------------------------------------------------
        if not generation_type or generation_type in [models.MasterModuleType.TRYON, models.MasterModuleType.THREE_SIXTY]:
            
            tryon_query = db.query(models.TryOnJob).filter(models.TryOnJob.user_id == current_user.id)
            tryon_jobs = tryon_query.order_by(models.TryOnJob.id.desc()).all()
            
            for job in tryon_jobs:
                # Handle JSON extraction safely for result_image_urls
                urls = []
                if job.result_image_urls:
                    urls = job.result_image_urls if isinstance(job.result_image_urls, list) else json.loads(job.result_image_urls)
                
                combined_results.append({
                    "job_id": job.id,
                    # Note: TryOn and 360 share this table, so we classify them broadly here
                    "job_type": models.MasterModuleType.TRYON.value, 
                    "status": job.status.value if hasattr(job.status, 'value') else job.status,
                    "fashn_job_id": job.fashn_job_id,
                    "input_parameters": {
                        "category": job.category.value if hasattr(job.category, 'value') else job.category,
                        "garment_url": job.garment_image_url,
                        "model_url": job.user_image_url
                    },
                    "generated_image_urls": urls,
                    # Fallback to empty string if missing to prevent sorting crashes
                    "created_at": job.created_at.isoformat() if getattr(job, 'created_at', None) else ""
                })

        # ---------------------------------------------------------
        # 2. FETCH OUTFIT BUILDER JOBS (Stored in OutfitJob Table)
        # ---------------------------------------------------------
        if not generation_type or generation_type == models.MasterModuleType.OUTFIT:
            
            outfit_query = db.query(models.OutfitJob).filter(models.OutfitJob.user_id == current_user.id)
            outfit_jobs = outfit_query.order_by(models.OutfitJob.id.desc()).all()
            
            for job in outfit_jobs:
                # Outfit jobs use a single string column for the result
                urls = [job.result_image_url] if job.result_image_url else []
                
                combined_results.append({
                    "job_id": job.id,
                    "job_type": models.MasterModuleType.OUTFIT.value,
                    "status": job.status.value if hasattr(job.status, 'value') else job.status,
                    "fashn_job_id": job.fashn_job_id,
                    "input_parameters": {
                        "styling_prompt": job.styling_prompt,
                        "model_url": job.person_image_url
                    },
                    "generated_image_urls": urls,
                    # OutfitJob schema lacks created_at, defaulting to empty string
                    "created_at": "" 
                })

        # ---------------------------------------------------------
        # 3. SORT & PAGINATE IN PYTHON
        # ---------------------------------------------------------
        # Sort by Job ID descending (Newest first) since OutfitJob lacks timestamps
        combined_results.sort(key=lambda x: x["job_id"], reverse=True)
        
        # Apply standard skip/limit pagination logic
        paginated_results = combined_results[skip : skip + limit]

        return StandardResponse(
            status=True,
            msg="User generations retrieved successfully.",
            data={
                "total": len(combined_results),
                "skip": skip,
                "limit": limit,
                "jobs": paginated_results
            }
        )

    except Exception as e:
        raise APIException(status_code=500, msg=f"Failed to fetch generations: {str(e)}")