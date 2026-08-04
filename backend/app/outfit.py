import asyncio
import logging
import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session

from . import models
from .database import get_db, SessionLocal
from .auth import get_current_user
from .utils import save_upload_file,download_and_save_remote_image
from .fashn_service import trigger_vton_job, check_vton_status
from .schemas import StandardResponse
from .exceptions import APIException
from .config import settings  # UPDATED: Importing settings directly

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outfit", tags=["Outfit Builder"])

# ==========================================
# ASYNCHRONOUS CHAINING ENGINE (SINGLE IMAGE)
# ==========================================
async def process_outfit_chain(
    job_id: int, 
    resolution: str, 
    output_format: str
):
    """
    Background worker executing sequential virtual try-on layering.
    Strictly handles a single output image pipeline.
    """
    db = SessionLocal()
    try:
        job = db.query(models.OutfitJob).filter(models.OutfitJob.id == job_id).first()
        if not job:
            logger.error(f"Background Outfit Job {job_id} not found in database.")
            return

        job.status = models.JobStatus.PROCESSING
        db.commit()

        current_base_image = job.person_image_url
        base_url = settings.BACKEND_URL.rstrip('/')

        layer_priority = {
            models.OutfitLayer.BOTTOM: 1,
            models.OutfitLayer.TOP: 2,
            models.OutfitLayer.OUTERWEAR: 3,
            models.OutfitLayer.ACCESSORY: 4,
            models.OutfitLayer.FOOTWEAR: 5
        }
        
        sorted_garments = sorted(job.garments, key=lambda g: layer_priority.get(g.layer_category, 99))
        total_layers = len(sorted_garments)

        if total_layers == 0:
            raise Exception("No valid garments linked to this outfit job.")

        logger.info(f"Initiating AI Chain for Job {job_id} | Layers: {total_layers}")

        for index, garment in enumerate(sorted_garments):
            logger.info(f"Processing Layer [{index + 1}/{total_layers}]: {garment.layer_category.value}")
            
            path_part = garment.closet_item.file_path.replace("\\", "/")
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"
            
            fashn_category = "tops"
            if garment.layer_category == models.OutfitLayer.BOTTOM:
                fashn_category = "bottoms"
            elif garment.layer_category in (models.OutfitLayer.TOP, models.OutfitLayer.OUTERWEAR):
                fashn_category = "tops"

            dynamic_prompt = job.styling_prompt if job.styling_prompt else ""
            if garment.layer_category == models.OutfitLayer.TOP:
                dynamic_prompt = f"tucked into pants, fitted at waist. {dynamic_prompt}".strip()
            elif garment.layer_category == models.OutfitLayer.OUTERWEAR:
                dynamic_prompt = f"worn open, unzipped, layered over shirt. {dynamic_prompt}".strip()

            fashn_id = await trigger_vton_job(
                model_image_url=current_base_image,
                garment_image_url=garment_url,
                category=fashn_category,
                garment_desc=dynamic_prompt,
                resolution=resolution,
                output_format=output_format,
                num_images=1  
            )
            
            single_result_url = None
            while True:
                await asyncio.sleep(4)  
                status, output_data = await check_vton_status(fashn_id)
                if status == "completed":
                    remote_url = output_data[0] if isinstance(output_data, list) else output_data
                    local_filename = await download_and_save_remote_image(remote_url)
                    single_result_url = f"{base_url}/static_uploads/{local_filename}"
                    break
                elif status == "failed":
                    raise Exception(f"FASHN generation failed on layer: {garment.layer_category.value}")
            
            current_base_image = single_result_url

        job.result_image_url = current_base_image
        job.status = models.JobStatus.COMPLETED
        db.commit()
        logger.info(f"Outfit Job {job_id} successfully chained and completed!")

    except Exception as e:
        logger.error(f"Outfit Chain Failure on Job {job_id}: {str(e)}", exc_info=True)
        if 'job' in locals() and job:
            job.status = models.JobStatus.FAILED
            db.commit()
    finally:
        db.close()


# ==========================================
# API ENDPOINTS
# ==========================================
@router.post("/generate", response_model=StandardResponse)
async def create_outfit_job(
    background_tasks: BackgroundTasks,
    person_image: Optional[UploadFile] = File(None),
    generated_model_job_id: Optional[int] = Form(None),
    top_closet_id: Optional[int] = Form(None),
    bottom_closet_id: Optional[int] = Form(None),
    outerwear_closet_id: Optional[int] = Form(None),
    outfit_desc: Optional[str] = Form(""),
    resolution: str = Form("1k"),
    output_format: str = Form("png"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"Multi-garment composition requested by User ID: {current_user.id}")
    base_url = settings.BACKEND_URL.rstrip('/')

    if not any([top_closet_id, bottom_closet_id, outerwear_closet_id]):
        raise APIException(status_code=400, msg="Outfit creation requires at least one closet garment layer selection.")
    
    person_url: Optional[str] = None
    
    if generated_model_job_id:
        logger.info(f"Resolving User's Generated AI Model from StudioJob ID: {generated_model_job_id}")
        studio_job = db.query(models.StudioJob).filter(
            models.StudioJob.id == generated_model_job_id,
            models.StudioJob.user_id == current_user.id,
            # models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            # models.StudioJob.status == models.JobStatus.COMPLETED,
            models.StudioJob.is_active == True
        ).first()
        
        if not studio_job or not studio_job.result_urls:
            raise APIException(status_code=404, msg="Generated model not found or job has not completed yet.")
            
        urls = studio_job.result_urls if isinstance(studio_job.result_urls, list) else json.loads(studio_job.result_urls)
        if not urls:
            raise APIException(status_code=404, msg="No images found in the generated model.")
            
        person_url = urls[0]
            
    elif person_image:
        logger.debug(f"Validating custom person_image: {person_image.filename}")
        if not person_image.content_type.startswith("image/"):
            raise APIException(status_code=400, msg="Invalid person_image format. Must be an image.")
            
        logger.info("Saving uploaded custom person image...")
        person_filename = save_upload_file(person_image)
        person_url = f"{base_url}/static_uploads/{person_filename}"
        
    if not person_url:
        raise APIException(
            status_code=400, 
            msg="Must provide either a person_image upload or a valid generated_model_job_id."
        )

    db_job = models.OutfitJob(
        user_id=current_user.id,
        person_image_url=person_url,
        status=models.JobStatus.PENDING,
        styling_prompt=outfit_desc
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    def attach_layer_relationship(closet_id: Optional[int], layer_type: models.OutfitLayer):
        if not closet_id:
            return
            
        item = db.query(models.ClosetItem).filter(
            models.ClosetItem.id == closet_id,
            models.ClosetItem.user_id == current_user.id
        ).first()
        
        if not item:
            raise APIException(
                status_code=404, 
                msg=f"Garment selection ID {closet_id} missing or unauthorized for this profile."
            )
            
        db_garment = models.OutfitGarment(
            outfit_job_id=db_job.id,
            closet_item_id=item.id,
            layer_category=layer_type
        )
        db.add(db_garment)

    try:
        attach_layer_relationship(top_closet_id, models.OutfitLayer.TOP)
        attach_layer_relationship(bottom_closet_id, models.OutfitLayer.BOTTOM)
        attach_layer_relationship(outerwear_closet_id, models.OutfitLayer.OUTERWEAR)
        db.commit()
    except APIException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Relational binding error on outfit mapping: {str(e)}")
        raise APIException(status_code=500, msg="Failed to bind relational metadata mapping arrays.")
    
    background_tasks.add_task(
        process_outfit_chain, 
        db_job.id, 
        resolution, 
        output_format
    )

    return StandardResponse(
        status=True,
        msg="Multi-garment composition successfully prioritized and queued.",
        data={"outfit_job_id": db_job.id, "status": db_job.status.value}
    )


@router.get("/{job_id}", response_model=StandardResponse)
async def get_outfit_status(
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    job = db.query(models.OutfitJob).filter(
        models.OutfitJob.id == job_id,
        models.OutfitJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise APIException(status_code=404, msg="Requested outfit tracking profile record not found.")
        
    return StandardResponse(
        status=True,
        msg="Composition status pulled successfully.",
        data={
            "id": job.id,
            "status": job.status.value,
            "result_image_url": job.result_image_url
        }
    )