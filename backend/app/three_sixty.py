import logging
from fastapi import APIRouter, Depends, Form, File, UploadFile
from sqlalchemy.orm import Session
from typing import Optional
from enum import Enum

from . import models
from .database import get_db
from .auth import get_current_user
from .utils import save_upload_file
from .fashn_service import trigger_vton_job, check_vton_status
from .schemas import StandardResponse
from .exceptions import APIException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/360", tags=["360 View Generator"])

class ViewPosition(str, Enum):
    FRONT = "front"
    SIDE = "side"
    BACK = "back"

@router.post("/generate", response_model=StandardResponse)
async def create_360_job(
    category: models.GarmentCategory = Form(...),
    position: ViewPosition = Form(...),              # 'front', 'side', or 'back'
    garment_desc: Optional[str] = Form(""),
    resolution: str = Form("1k"),
    output_format: str = Form("png"),
    person_image: UploadFile = File(...),
    garment_image: Optional[UploadFile] = File(None),
    closet_item_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"User {current_user.id} requested a 360 try-on job for position: {position.value}")
    base_url = "https://vton-backend.falcondetectives.com"
    
    # 1. File Type Validation
    if not person_image.content_type.startswith("image/"):
        raise APIException(status_code=400, msg="Invalid person_image format. Must be an image file.")
    if garment_image and not garment_image.content_type.startswith("image/"):
        raise APIException(status_code=400, msg="Invalid garment_image format. Must be an image file.")

    try:
        # 2. Resolve Garment Resource (Closet ID vs Raw Upload)
        if closet_item_id:
            item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_item_id, 
                models.ClosetItem.user_id == current_user.id
            ).first()
            if not item:
                raise APIException(status_code=404, msg="Selected closet garment not found or unauthorized.")
            
            path_part = item.file_path.replace("\\", "/") 
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"
            
        elif garment_image:
            garment_filename = save_upload_file(garment_image)
            garment_url = f"{base_url}/static_uploads/{garment_filename}"
        else:
            raise APIException(status_code=400, msg="Must provide either a garment_image or a valid closet_item_id.")

        # 3. Save Model Image Canvas
        person_filename = save_upload_file(person_image)
        person_url = f"{base_url}/static_uploads/{person_filename}"

        # 4. Construct Dynamic Suffix for the Position Perspective
        # This appends instructions ensuring FASHN aligns the try-on to the requested angle
        angle_instruction = f"{position.value} view"
        final_description = angle_instruction
        if garment_desc and garment_desc.strip():
            final_description = f"{angle_instruction}, {garment_desc.strip()}"

        # 5. Create Database Job Profile Track Entry
        # Note: Ensure your Job model has a 'position' column if you want to track it persistently!
        db_job = models.TryOnJob(
            user_id=current_user.id,
            category=category,
            user_image_url=person_url,
            garment_image_url=garment_url,
            status=models.JobStatus.PENDING
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)

        # 6. Dispatch parameters to core FASHN service
        fashn_job_id = await trigger_vton_job(
            model_image_url=person_url, 
            garment_image_url=garment_url, 
            category=category.value, 
            garment_desc=final_description,
            resolution=resolution,
            output_format=output_format,
            num_images=1 # Safely kept at 1 as per single-image design rules
        )
        
        db_job.fashn_job_id = fashn_job_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()

        return StandardResponse(
            status=True, 
            msg=f"Try-on job for {position.value} view initiated successfully.", 
            data={"job_id": db_job.id, "status": db_job.status.value}
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"360 generation registration crash: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to register 360 AI task pipeline.")


@router.get("/status/{job_id}", response_model=StandardResponse)
async def get_360_job_status(
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Standardized poll endpoint reading FASHN AI state for 360 rendering tasks.
    """
    logger.info(f"--- 360 STATUS POLL --- User {current_user.id} checking Job ID: {job_id}")
    try:
        db_job = db.query(models.TryOnJob).filter(
            models.TryOnJob.id == job_id, 
            models.TryOnJob.user_id == current_user.id
        ).first()
        
        if not db_job:
            logger.warning(f"360 Status poll failed: Job {job_id} not found.")
            raise APIException(status_code=404, msg="Requested 360 generation task profile not found.")

        # 1. Handle Cached/Completed State First
        if db_job.status in [models.JobStatus.COMPLETED, models.JobStatus.FAILED]:
            logger.info(f"360 Job {job_id} is already {db_job.status.value}. Returning cached record.")
            return StandardResponse(
                status=True, 
                msg="Job execution record analyzed.", 
                data={
                    "id": db_job.id,
                    "status": db_job.status.value,
                    "result_image_urls": db_job.result_image_urls # Safe JSON array
                }
            )

        # 2. Poll FASHN if still processing
        if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
            logger.info(f"360 Job {job_id} is PROCESSING. Pinging FASHN API...")
            fashn_status, output_data = await check_vton_status(db_job.fashn_job_id)

            if fashn_status == "completed":
                db_job.status = models.JobStatus.COMPLETED
                
                # Assign the raw array directly to your new JSON column!
                db_job.result_image_urls = output_data 
                
                db.commit()
                logger.info(f"SUCCESS: 360 Job {job_id} finished generating!")
                
            elif fashn_status == "failed":
                db_job.status = models.JobStatus.FAILED
                db.commit()
                logger.error(f"FAILURE: FASHN AI Engine reported a failure for 360 Job {job_id}.")

        # 3. Explicit Dictionary Mapping for JSON Serialization
        return StandardResponse(
            status=True, 
            msg="Job execution record analyzed.", 
            data={
                "id": db_job.id,
                "status": db_job.status.value, # Unpacks the Enum to a string
                "result_image_urls": db_job.result_image_urls
            }
        )

    except APIException:
        raise
    except Exception as e:
        logger.error(f"Error handling 360 status retrieval: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal server exception reading pipeline profile indicators.")