import json
import logging
import asyncio
from typing import Optional, Dict

from fastapi import APIRouter, Depends, Form, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session

from . import models
from .database import get_db, SessionLocal
from .auth import get_current_user
from .utils import save_upload_file, download_and_save_remote_image
from .fashn_service import trigger_vton_job, check_vton_status, ensure_fashn_credits_available
from .schemas import StandardResponse
from .exceptions import APIException
from .config import settings
from .gatekeeper import PlanGatekeeper, SubscriptionTransactionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/360", tags=["360 View Generator"])

async def process_dynamic_generation_chain(
    job_id: int,
    user_id: int,
    cost: int,
    person_urls: Dict[str, str],  
    garment_url: str,
    category: models.GarmentCategory,
    garment_desc: Optional[str],
    resolution: str,
    output_format: str
):
    db = SessionLocal()
    base_url = settings.BACKEND_URL.rstrip("/")
    try:
        db_job = db.query(models.TryOnJob).filter(models.TryOnJob.id == job_id).first()
        if not db_job:
            logger.error(f"[360 WORKER] Job {job_id} not found.")
            return

        db_job.status = models.JobStatus.PROCESSING
        db.commit()

        # 1. Dispatch parallel requests to FASHN
        async def trigger_single_position(pos: str, person_url: str):
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
                num_images=1
            )
            return pos, fashn_id

        trigger_results = await asyncio.gather(
            *[trigger_single_position(pos, url) for pos, url in person_urls.items()],
            return_exceptions=True
        )

        position_fashn_ids: Dict[str, str] = {}
        for res in trigger_results:
            if isinstance(res, Exception):
                logger.error(f"[360 WORKER] Failed to trigger view for Job {job_id}: {res}")
                raise Exception(f"Failed to dispatch position trigger: {str(res)}")
                
            pos, fashn_id = res
            position_fashn_ids[pos] = fashn_id

        db_job.fashn_job_id = json.dumps(position_fashn_ids)
        db.commit()

        # 2. Polling loop
        completed_results: Dict[str, str] = {}
        pending_positions = dict(position_fashn_ids)
        max_polls, poll_count = 60, 0

        while pending_positions and poll_count < max_polls:
            await asyncio.sleep(3)
            poll_count += 1

            positions_to_check = list(pending_positions.items())
            check_tasks = [check_vton_status(f_id) for _, f_id in positions_to_check]
            statuses = await asyncio.gather(*check_tasks, return_exceptions=True)

            for (pos, f_id), res in zip(positions_to_check, statuses):
                if isinstance(res, Exception): 
                    continue
                status, output = res
                if status == "completed":
                    remote_url = output[0] if isinstance(output, list) else output
                    local_filename = await download_and_save_remote_image(remote_url)
                    completed_results[pos] = f"{base_url}/static_uploads/{local_filename}"
                    del pending_positions[pos]
                elif status == "failed":
                    raise Exception(f"AI Engine reported failure for angle: {pos}")

        if pending_positions:
            raise Exception("Polling timeout reached before all angles finished rendering.")

        # 3. Finalize
        db_job.result_image_urls = completed_results
        db_job.status = models.JobStatus.COMPLETED
        db.commit()
        logger.info(f"[360 WORKER] Job {job_id} successfully completed for angles: {list(completed_results.keys())}")

    except Exception as e:
        logger.error(f"[360 WORKER] Crash on Job {job_id}: {str(e)}", exc_info=True)
        if 'db_job' in locals() and db_job:
            db_job.status = models.JobStatus.FAILED
            db.commit()

        sub = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user_id,
            models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
        ).first()
        if sub:
            SubscriptionTransactionManager.refund_resources(
                db, sub, cost, "three_sixty", reference_id=job_id, reason=str(e)
            )
    finally:
        db.close()
        
        
@router.post("/generate", response_model=StandardResponse)
async def create_360_job(
    background_tasks: BackgroundTasks,
    category: models.GarmentCategory = Form(...),
    garment_desc: Optional[str] = Form(""),
    resolution: str = Form("1k"),
    output_format: str = Form("png"),

    garment_image: Optional[UploadFile] = File(None),
    closet_item_id: Optional[int] = Form(None),

    person_image_front: Optional[UploadFile] = File(None),
    person_image_back: Optional[UploadFile] = File(None),
    person_image_side: Optional[UploadFile] = File(None),

    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper())
):
    base_url = settings.BACKEND_URL.rstrip("/")

    # Pre-flight check on master wallet
    await ensure_fashn_credits_available(min_required=1.0)

    try:
        # 1. Resolve Garment Source
        if closet_item_id:
            item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_item_id,
                models.ClosetItem.user_id == subscription.user_id
            ).first()
            if not item:
                raise APIException(status_code=200, msg="Selected closet garment not found or unauthorized.")

            path_part = item.file_path.replace("\\", "/")
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"

        elif garment_image:
            if not garment_image.content_type.startswith("image/"):
                raise APIException(status_code=200, msg="Invalid garment_image format. Must be an image file.")
            garment_filename = save_upload_file(garment_image)
            garment_url = f"{base_url}/static_uploads/{garment_filename}"
            
        else:
            raise APIException(status_code=200, msg="Must provide either a garment_image upload or a valid closet_item_id.")

        # 2. Process person angles
        person_urls: Dict[str, str] = {}
        
        angle_uploads = {
            "front": person_image_front,
            "back": person_image_back,
            "side": person_image_side
        }

        for pos, file_obj in angle_uploads.items():
            if file_obj:
                if not file_obj.content_type.startswith("image/"):
                    raise APIException(status_code=200, msg=f"Invalid file for person_image_{pos}.")
                
                filename = save_upload_file(file_obj)
                person_urls[pos] = f"{base_url}/static_uploads/{filename}"

        if not person_urls:
            raise APIException(status_code=200, msg="You must upload at least one person image (front, back, or side).")
        
        # 3. Feature Gate Rule: Silver Tier restriction on multi-angle views
        view_360_mode = subscription.plan_snapshot.get("view_360_mode", "single_image")
        if len(person_urls) > 1 and view_360_mode == "single_image":
            raise APIException(
                status_code=200, 
                msg="Multi-angle 360° generation (front, back, side) is restricted to Gold and Platinum plans. Silver plan supports single image view only."
            )
            
        # 4. Calculate Billing (2 credits per requested angle)
        cost = len(person_urls) * 2

        # 5. Persist Tracking Record
        db_job = models.TryOnJob(
            user_id=subscription.user_id,
            category=category,
            user_image_url=list(person_urls.values())[0], 
            garment_image_url=garment_url,
            status=models.JobStatus.PENDING
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        
        # 6. Deduct Credits Atomically
        SubscriptionTransactionManager.deduct_resources(
            db, subscription, cost, "three_sixty", reference_id=db_job.id
        )

        # 7. Dispatch Background Processing Chain
        background_tasks.add_task(
            process_dynamic_generation_chain,
            job_id=db_job.id,
            user_id=subscription.user_id,
            cost=cost,
            person_urls=person_urls,
            garment_url=garment_url,
            category=category,
            garment_desc=garment_desc,
            resolution=resolution,
            output_format=output_format
        )
        return StandardResponse(
            status=True,
            msg=f"360° generation initiated for angles: {list(person_urls.keys())}.",
            data={
                "job_id": db_job.id,
                "status": db_job.status.value,
                "requested_angles": list(person_urls.keys()),
                "credits_deducted": cost
            }
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"360 generation crash: {str(e)}", exc_info=True)
        raise APIException(status_code=200, msg="Failed to register 360 AI task pipeline.")