import logging
import asyncio
import json
from fastapi import APIRouter, Depends, Form, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, Dict, List

from app import models
from app.database import get_db, SessionLocal
from app.auth import get_current_user
from app.utils import save_upload_file
from app.fashn_service import trigger_vton_job, check_vton_status
from app.schemas import StandardResponse
from app.exceptions import APIException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/360", tags=["360 View Generator"])

# Valid angles for validation
VALID_POSITIONS = ["front", "back", "side"]

async def process_dynamic_generation_chain(
    job_id: int,
    person_url: str,
    garment_url: str,
    category: models.GarmentCategory,
    garment_desc: Optional[str],
    resolution: str,
    output_format: str,
    target_positions: List[str]  # NEW: Receives the specific angles to generate
):
    """
    Background worker that dynamically dispatches FASHN try-on jobs for ONLY 
    the requested positions in parallel.
    """
    db = SessionLocal()
    try:
        db_job = db.query(models.TryOnJob).filter(models.TryOnJob.id == job_id).first() #[cite: 2]
        if not db_job:
            logger.error(f"[360 WORKER] Job {job_id} not found in database.")
            return

        db_job.status = models.JobStatus.PROCESSING #[cite: 2]
        db.commit()

        # 1. Prepare parallel trigger tasks only for the requested positions
        async def trigger_single_position(pos: str):
            angle_instruction = f"{pos} view"
            final_description = (
                f"{angle_instruction}, {garment_desc.strip()}"
                if garment_desc and garment_desc.strip()
                else angle_instruction
            )
            
            fashn_id = await trigger_vton_job(
                model_image_url=person_url,
                garment_image_url=garment_url,
                category=category.value,
                garment_desc=final_description,
                resolution=resolution,
                output_format=output_format,
                num_images=1 #[cite: 1]
            )
            return pos, fashn_id

        # Dispatch triggers concurrently for requested angles
        logger.info(f"[360 WORKER] Dispatching parallel FASHN jobs for Job {job_id} on angles: {target_positions}...")
        trigger_results = await asyncio.gather(
            *[trigger_single_position(pos) for pos in target_positions],
            return_exceptions=True
        )

        position_fashn_ids: Dict[str, str] = {}
        for res in trigger_results:
            if isinstance(res, Exception):
                logger.error(f"[360 WORKER] Failed to trigger one of the views: {res}")
                db_job.status = models.JobStatus.FAILED #[cite: 2]
                db.commit()
                return
            pos, fashn_id = res
            position_fashn_ids[pos] = fashn_id

        # Save FASHN IDs to database as JSON
        db_job.fashn_job_id = json.dumps(position_fashn_ids) #[cite: 2]
        db.commit()

        # 2. Poll the triggered tasks until complete
        completed_results: Dict[str, str] = {}
        pending_positions = dict(position_fashn_ids)

        max_polls = 60  # Timeout protection (~3 minutes total)
        poll_count = 0

        while pending_positions and poll_count < max_polls:
            await asyncio.sleep(3)
            poll_count += 1

            for pos, f_id in list(pending_positions.items()):
                status, output = await check_vton_status(f_id) #[cite: 3]

                if status == "completed":
                    result_url = output[0] if isinstance(output, list) else output
                    completed_results[pos] = result_url
                    del pending_positions[pos]
                elif status == "failed":
                    logger.error(f"[360 WORKER] Position {pos} failed on FASHN side.")
                    db_job.status = models.JobStatus.FAILED #[cite: 2]
                    db.commit()
                    return

        if pending_positions:
            logger.error(f"[360 WORKER] Polling timed out for Job {job_id}.")
            db_job.status = models.JobStatus.FAILED #[cite: 2]
            db.commit()
            return

        # 3. Save structured JSON dictionary into result_image_urls
        db_job.result_image_urls = completed_results #[cite: 2]
        db_job.status = models.JobStatus.COMPLETED #[cite: 2]
        db.commit()
        logger.info(f"[360 WORKER] Job {job_id} successfully generated views: {target_positions}")

    except Exception as e:
        logger.error(f"[360 WORKER] Exception during background chain for job {job_id}: {str(e)}", exc_info=True)
        if 'db_job' in locals() and db_job:
            db_job.status = models.JobStatus.FAILED #[cite: 2]
            db.commit()
    finally:
        db.close()


@router.post("/generate", response_model=StandardResponse) #[cite: 1]
async def create_360_job(
    background_tasks: BackgroundTasks,
    category: models.GarmentCategory = Form(...), #[cite: 1]
    positions: str = Form("front", description="Comma-separated angles, e.g. 'front,back,side'"), # NEW: Dynamic input
    garment_desc: Optional[str] = Form(""), #[cite: 1]
    resolution: str = Form("1k"), #[cite: 1]
    output_format: str = Form("png"), #[cite: 1]
    person_image: UploadFile = File(...), #[cite: 1]
    garment_image: Optional[UploadFile] = File(None), #[cite: 1]
    closet_item_id: Optional[int] = Form(None), #[cite: 1]
    db: Session = Depends(get_db), #[cite: 1]
    current_user: models.User = Depends(get_current_user) #[cite: 1]
):
    logger.info(f"User {current_user.id} requested generation for angles: {positions}")
    base_url = "https://vton-backend.falcondetectives.com" #[cite: 1]

    # --- NEW: Parse and Validate Target Positions ---
    # Convert comma-separated string to a clean list: "front, back" -> ["front", "back"]
    parsed_positions = [p.strip().lower() for p in positions.split(",") if p.strip()]
    
    # Filter only valid angles
    target_positions = [p for p in parsed_positions if p in VALID_POSITIONS]
    
    # Fallback to "front" if nothing valid was passed
    if not target_positions:
        target_positions = ["front"]
    # ------------------------------------------------

    # 1. File Type Validation
    if not person_image.content_type.startswith("image/"): #[cite: 1]
        raise APIException(status_code=400, msg="Invalid person_image format. Must be an image file.") #[cite: 1]
    if garment_image and not garment_image.content_type.startswith("image/"): #[cite: 1]
        raise APIException(status_code=400, msg="Invalid garment_image format. Must be an image file.") #[cite: 1]

    try:
        # 2. Resolve Garment Resource (Closet ID vs Raw Upload)
        if closet_item_id: #[cite: 1]
            item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_item_id, #[cite: 1]
                models.ClosetItem.user_id == current_user.id #[cite: 1]
            ).first() #[cite: 1]
            if not item: #[cite: 1]
                raise APIException(status_code=404, msg="Selected closet garment not found or unauthorized.") #[cite: 1]

            path_part = item.file_path.replace("\\", "/") #[cite: 1]
            if not path_part.startswith("/"): #[cite: 1]
                path_part = "/" + path_part #[cite: 1]
            garment_url = f"{base_url}{path_part}" #[cite: 1]

        elif garment_image: #[cite: 1]
            garment_filename = save_upload_file(garment_image) #[cite: 1]
            garment_url = f"{base_url}/static_uploads/{garment_filename}" #[cite: 1]
        else: #[cite: 1]
            raise APIException(status_code=400, msg="Must provide either a garment_image or a valid closet_item_id.") #[cite: 1]

        # 3. Save Model Image Canvas
        person_filename = save_upload_file(person_image) #[cite: 1]
        person_url = f"{base_url}/static_uploads/{person_filename}" #[cite: 1]

        # 4. Create Initial Database Entry
        db_job = models.TryOnJob(
            user_id=current_user.id, #[cite: 2]
            category=category, #[cite: 2]
            user_image_url=person_url, #[cite: 2]
            garment_image_url=garment_url, #[cite: 2]
            status=models.JobStatus.PENDING #[cite: 2]
        )
        db.add(db_job) #[cite: 1]
        db.commit() #[cite: 1]
        db.refresh(db_job) #[cite: 1]

        # 5. Dispatch Async Background Task for the SPECIFIC angles
        background_tasks.add_task(
            process_dynamic_generation_chain,
            job_id=db_job.id,
            person_url=person_url,
            garment_url=garment_url,
            category=category,
            garment_desc=garment_desc,
            resolution=resolution,
            output_format=output_format,
            target_positions=target_positions # Passed cleanly to the worker
        )

        # 6. Immediate Response Cycle
        return StandardResponse(
            status=True,
            msg=f"View generation initiated for angles: {', '.join(target_positions)}.",
            data={"job_id": db_job.id, "status": db_job.status.value, "requested_angles": target_positions}
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback() #[cite: 1]
        logger.error(f"Generation registration crash: {str(e)}", exc_info=True) #[cite: 1]
        raise APIException(status_code=500, msg="Failed to register AI task pipeline.") #[cite: 1]


@router.get("/status/{job_id}", response_model=StandardResponse) #[cite: 1]
async def get_360_job_status(
    job_id: int, #[cite: 1]
    db: Session = Depends(get_db), #[cite: 1]
    current_user: models.User = Depends(get_current_user) #[cite: 1]
):
    logger.info(f"User {current_user.id} checking Job ID: {job_id}") #[cite: 1]
    try:
        db_job = db.query(models.TryOnJob).filter(
            models.TryOnJob.id == job_id, #[cite: 1]
            models.TryOnJob.user_id == current_user.id #[cite: 1]
        ).first() #[cite: 1]

        if not db_job: #[cite: 1]
            raise APIException(status_code=404, msg="Requested generation task profile not found.") #[cite: 1]

        return StandardResponse(
            status=True, #[cite: 1]
            msg="Job execution record retrieved.", #[cite: 1]
            data={
                "id": db_job.id, #[cite: 1]
                "status": db_job.status.value, #[cite: 1]
                # Result dict dynamically scales based on requested angles 
                # e.g. {"front": "url"} OR {"front": "url", "back": "url", "side": "url"}
                "result_image_urls": db_job.result_image_urls #[cite: 2] 
            }
        )

    except APIException:
        raise
    except Exception as e:
        logger.error(f"Error reading status: {str(e)}", exc_info=True) #[cite: 1]
        raise APIException(status_code=500, msg="Internal server exception reading pipeline profile indicators.") #[cite: 1]