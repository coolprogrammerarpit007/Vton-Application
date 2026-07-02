import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional

from . import models
from .database import get_db, SessionLocal
from .auth import get_current_user
from .utils import save_upload_file
from .fashn_service import trigger_vton_job, check_vton_status

# Configure logging to write to both the console AND a file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("vton_debug.log"), # This creates the log file
        logging.StreamHandler()                # This keeps it printing in the terminal
    ]
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outfit", tags=["Outfit Builder"])

# ==========================================
# ASYNCHRONOUS CHAINING ENGINE
# ==========================================
async def process_outfit_chain(job_id: int):
    """
    Background worker that executes the FASHN.ai generation chain sequentially.
    """
    # Create a fresh database session for the background thread
    db = SessionLocal()
    try:
        job = db.query(models.OutfitJob).filter(models.OutfitJob.id == job_id).first()
        if not job:
            return

        job.status = models.JobStatus.PROCESSING
        db.commit()

        # 1. Establish the base canvas
        current_base_image = job.person_image_url
        base_url = "https://vton-backend.falcondetectives.com"

        # 2. Define the logical rendering hierarchy 
        # (Always put pants on before shirts, shirts before jackets)
        layer_priority = {
            models.OutfitLayer.BOTTOM: 1,
            models.OutfitLayer.TOP: 2,
            models.OutfitLayer.OUTERWEAR: 3,
            models.OutfitLayer.ACCESSORY: 4,
            models.OutfitLayer.FOOTWEAR: 5
        }
        
        # Sort garments based on the priority dictionary
        sorted_garments = sorted(job.garments, key=lambda g: layer_priority.get(g.layer_category, 99))

        logger.info(f"Initiating AI Chain for Job {job_id} | Layers: {len(sorted_garments)}")

        # 3. Execute the Chain Loop
        for garment in sorted_garments:
            logger.info(f"Processing Layer: {garment.layer_category.value}")
            
            # Construct the absolute URL for the garment
            path_part = garment.closet_item.file_path.replace("\\", "/")
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"
            
            # --- NEW: Map internal layers to strict FASHN API categories ---
            fashn_category = "tops" # Default fallback
            if garment.layer_category == models.OutfitLayer.BOTTOM:
                fashn_category = "bottoms"
            elif garment.layer_category == models.OutfitLayer.TOP:
                fashn_category = "tops"
            elif garment.layer_category == models.OutfitLayer.OUTERWEAR:
                fashn_category = "tops" # FASHN treats jackets/coats as tops
            # -------------------------------------------------------------
            
            # --- NEW: Dynamic Layer Prompting ---
            # We intercept the prompt to give the AI physical rules for each layer
            dynamic_prompt = job.styling_prompt if job.styling_prompt else ""
            
            if garment.layer_category == models.OutfitLayer.TOP:
                dynamic_prompt = f"tucked into pants, fitted at waist. {dynamic_prompt}".strip()
            elif garment.layer_category == models.OutfitLayer.OUTERWEAR:
                dynamic_prompt = f"worn open, unzipped, layered over shirt. {dynamic_prompt}".strip()
            # ------------------------------------
            
            # Fire the request to FASHN.ai
            fashn_id = await trigger_vton_job(
                model_image_url=current_base_image,
                garment_image_url=garment_url,
                category=garment.layer_category.value,
                garment_desc=dynamic_prompt  # dynamic altered prompt passed to FASHN.ai
            )
            
            # 4. Polling Sub-Loop: Wait for this specific layer to finish
            result_url = None
            while True:
                await asyncio.sleep(4) # Poll every 4 seconds to respect rate limits
                status, url = await check_vton_status(fashn_id)
                
                if status == "completed":
                    result_url = url
                    break
                elif status == "failed":
                    raise Exception(f"FASHN generation failed on layer: {garment.layer_category.value}")
            
            # 5. Overwrite the base canvas with the newly generated layered image
            current_base_image = result_url

        # 6. Chain Complete: Save final state to database
        job.result_image_url = current_base_image
        job.status = models.JobStatus.COMPLETED
        db.commit()
        logger.info(f"Outfit Job {job_id} successfully chained and completed!")

    except Exception as e:
        logger.error(f"Outfit Chain Error on Job {job_id}: {str(e)}", exc_info=True)
        job.status = models.JobStatus.FAILED
        db.commit()
    finally:
        db.close()


# ==========================================
# API ROUTE
# ==========================================
@router.post("/generate")
async def create_outfit_job(
    background_tasks: BackgroundTasks, # Injects FastAPI Background Task manager
    person_image: UploadFile = File(...),
    top_closet_id: Optional[int] = Form(None),
    bottom_closet_id: Optional[int] = Form(None),
    outerwear_closet_id: Optional[int] = Form(None),
    outfit_desc: Optional[str] = Form(""),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    base_url = "https://vton-backend.falcondetectives.com"
    logger.info(f"Multi-garment job requested by User {current_user.id}")

    try:
        person_filename = save_upload_file(person_image)
        person_url = f"{base_url}/static_uploads/{person_filename}"
    except Exception:
        logger.error("Failed to save person image", exc_info=True)
        raise HTTPException(status_code=500, detail="Error saving person image")

    # Create Parent Job Record
    db_job = models.OutfitJob(
        user_id=current_user.id,
        person_image_url=person_url,
        status=models.JobStatus.PENDING,
        styling_prompt=outfit_desc
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    # Attach layer relationships
    def add_layer(closet_id, layer_type):
        if closet_id:
            item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_id,
                models.ClosetItem.user_id == current_user.id
            ).first()
            if item:
                db_garment = models.OutfitGarment(
                    outfit_job_id=db_job.id,
                    closet_item_id=item.id,
                    layer_category=layer_type
                )
                db.add(db_garment)

    add_layer(top_closet_id, models.OutfitLayer.TOP)
    add_layer(bottom_closet_id, models.OutfitLayer.BOTTOM)
    add_layer(outerwear_closet_id, models.OutfitLayer.OUTERWEAR)
    db.commit()

    # Dispatch the complex chain into the background
    background_tasks.add_task(process_outfit_chain, db_job.id)

    # Return immediately to the frontend
    return {"message": "Job queued successfully", "id": db_job.id}


@router.get("/{job_id}")
async def get_outfit_status(job_id: int, db: Session = Depends(get_db)):
    """
    Frontend polling endpoint. Just reads the local database.
    """
    job = db.query(models.OutfitJob).filter(models.OutfitJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return {
        "id": job.id,
        "status": job.status.value,
        "result_image_url": job.result_image_url
    }