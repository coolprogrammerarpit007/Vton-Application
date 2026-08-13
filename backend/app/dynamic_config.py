import logging
import json
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from . import models, schemas
from .database import get_db
from .schemas import StandardResponse
from .config import settings
from app.exceptions import APIException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["Dynamic Configurations"])

# ==============================================================================
# 1. GET PLATFORMS & GARMENT SEGMENTS
# ==============================================================================
@router.get("/platforms", response_model=StandardResponse)
def get_platforms_config(db: Session = Depends(get_db)):
    try:
        # OPTIMIZATION: joinedload fetches platforms and ratios in a single query
        platforms = (
            db.query(models.Platform)
            .options(joinedload(models.Platform.aspect_ratios))
            .filter(models.Platform.is_active == True)
            .all()
        )
      
        formatted_platforms = []
        for p in platforms:
            ratios = [
                {
                    "id": r.id,
                    "ratio": r.ratio,
                    # "default_width": r.default_width,
                    # "default_height": r.default_height,
                    "is_default": r.is_default
                } for r in p.aspect_ratios
            ]

            formatted_platforms.append({
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "aspect_ratios": ratios
            })

        return StandardResponse(
            status=True,
            msg="Platform and ratio configurations retrieved successfully",
            data={"platforms": formatted_platforms}
        )
    except Exception as e:
        logger.error(f"Error fetching platform configs: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to retrieve platform configs.")

# ==============================================================================
# 2. GET AI MODEL PERSONAS
# ==============================================================================
@router.get("/models", response_model=StandardResponse)
def get_model_personas(db: Session = Depends(get_db)):
    try:
        personas = (
            db.query(models.ModelPersona)
            .filter(models.ModelPersona.is_active == True)
            .all()
        )
        
        base_url = settings.BACKEND_URL.rstrip("/")
        formatted_personas = []
        
        for p in personas:
            preview = p.preview_image_url.replace("\\", "/")
            if not preview.startswith("http"):
                preview = f"{base_url}/{preview.lstrip('/')}"

            # OPTIMIZATION: Safe JSON parsing fallback
            attributes = {}
            if isinstance(p.attributes_json, dict):
                attributes = p.attributes_json
            elif p.attributes_json:
                try:
                    attributes = json.loads(p.attributes_json)
                except json.JSONDecodeError:
                    pass

            formatted_personas.append({
                "id": p.id,
                "name": p.name,
                "age": p.age,
                "gender": p.gender,
                "preview_image_url": preview,
                "attributes": attributes
            })

        return StandardResponse(
            status=True,
            msg="Model personas retrieved successfully",
            data=formatted_personas
        )
    except Exception as e:
        logger.error(f"Error fetching model personas: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to retrieve model personas.")

# ==============================================================================
# 3. UNIFIED ALL-IN-ONE CONFIG API (For Initial App Loading)
# ==============================================================================
@router.get("/all", response_model=StandardResponse)
def get_all_configs(db: Session = Depends(get_db)):
    """
    Returns platform matrix, garment categories, and model personas in a single round-trip.
    """
    platforms_res = get_platforms_config(db=db)
    models_res = get_model_personas(db=db)

    return StandardResponse(
        status=True,
        msg="All application configurations loaded successfully",
        data={
            "platform_matrix": platforms_res.data.get("platforms", []),
            "models_library": models_res.data
        }
    )
    
# ==============================================================================
# 4. GET TRY-ON STYLING PROMPT PRESETS
# ==============================================================================
@router.get("/tryon-prompts", response_model=StandardResponse)
def get_tryon_prompts(db: Session = Depends(get_db)):
    try:
        prompts = db.query(models.TryonPromptPreset).filter(
            models.TryonPromptPreset.is_active == True
        ).all()

        grouped_prompts = {"tops": [], "bottoms": [], "outerwear": []}
        
        # OPTIMIZATION: Clean dict appending using setdefault
        for p in prompts:
            grouped_prompts.setdefault(p.category, []).append({
                "id": p.id,
                "prompt_text": p.prompt_text
            })

        return StandardResponse(
            status=True,
            msg="Try-on styling prompts retrieved successfully",
            data=grouped_prompts
        )
        
    except Exception as e:
        logger.error(f"Error fetching tryon prompts: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to retrieve styling prompts.")
    
@router.get("/prompts", response_model=schemas.PromptTemplateResponse)
def get_dynamic_prompts(
    job_type: Optional[models.StudioJobType] = Query(None, description="Filter prompts by specific AI tool"),
    db: Session = Depends(get_db)
):
    try:
        query = db.query(models.PromptTemplate).filter(models.PromptTemplate.is_active == True)
        
        if job_type:
            query = query.filter(models.PromptTemplate.job_type == job_type)
            
        prompts = query.order_by(models.PromptTemplate.id.asc()).all()
        
        return schemas.PromptTemplateResponse(
            status=True,
            msg="Prompts retrieved successfully",
            data=prompts
        )
    except Exception as e:
        raise APIException(status_code=500, msg=f"Failed to fetch prompts: {str(e)}")