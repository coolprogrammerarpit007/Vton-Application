import logging
import asyncio
from fastapi import APIRouter, Depends, Form, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional

from . import models
from .database import get_db, SessionLocal
from .auth import get_current_user
from .utils import save_upload_file
from .fashn_service import trigger_generic_fashn_job, check_vton_status
from .schemas import StandardResponse
from .exceptions import APIException



logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studio", tags=["AI Creative Studio"])
base_url = "https://vton-backend.falcondetectives.com"

def validate_image(file: UploadFile):
    if not file.content_type.startswith("image/"):
        raise APIException(status_code=400, msg="Invalid file format. Must be an image.")

async def process_upload(file: UploadFile) -> str:
    validate_image(file)
    filename = save_upload_file(file)
    return f"{base_url}/static_uploads/{filename}"

# ==========================================
# 1. PRODUCT TO MODEL (Garment -> AI Human)
# ==========================================
@router.post("/product-to-model", response_model=StandardResponse)
async def product_to_model(
    garment_image: UploadFile = File(...),                                 # Required: The product
    image_prompt: Optional[UploadFile] = File(None),                       # Optional: Pose/Style inspiration
    face_reference: Optional[UploadFile] = File(None),                     # Optional: Face identity swap
    background_reference: Optional[UploadFile] = File(None),               # Optional: Specific background scene
    prompt: Optional[str] = Form(None),                                    # Optional: Text styling
    face_reference_mode: str = Form("match_reference"),                    # 'match_base' or 'match_reference'
    aspect_ratio: Optional[str] = Form(None),                              # '1:1', '3:4', '16:9', etc.
    resolution: str = Form("1k"),                                          # '1k', '2k', or '4k'
    generation_mode: Optional[str] = Form(None),                           # 'fast', 'balanced', or 'quality'
    num_images: int = Form(1),                                             # 1 to 4
    output_format: str = Form("png"),                                      # 'png' or 'jpeg'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # 1. Process Required Upload
    garment_url = await process_upload(garment_image)
    
    # 2. Base Payload
    input_data = {
        "product_image": garment_url,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }
    
    # 3. Process Optional Uploads and dynamically build payload
    if image_prompt:
        input_data["image_prompt"] = await process_upload(image_prompt)
        
    if background_reference:
        input_data["background_reference"] = await process_upload(background_reference)
        
    if face_reference:
        input_data["face_reference"] = await process_upload(face_reference)
        input_data["face_reference_mode"] = face_reference_mode
        
    # 4. Append Optional Form Data
    if prompt:
        input_data["prompt"] = prompt
    if aspect_ratio:
        input_data["aspect_ratio"] = aspect_ratio
    if generation_mode:
        input_data["generation_mode"] = generation_mode

    # 5. Save tracking state to DB
    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.PRODUCT_TO_MODEL,
        input_data=input_data  # Preserves all uploaded URLs and exact parameters used
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    try:
        # 6. Trigger FASHN AI Engine
        fashn_id = await trigger_generic_fashn_job(
            model_name="product-to-model",
            inputs=input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        
        return StandardResponse(
            status=True, 
            msg="Product-to-Model job started", 
            data={"job_id": db_job.id, "status": db_job.status.value}
        )
    except Exception as e:
        db.rollback()
        raise APIException(status_code=500, msg=f"AI Engine failed: {str(e)}")
# ==========================================
# 2. MODEL SWAP (Change face/body type)
# ==========================================
@router.post("/model-swap", response_model=StandardResponse)
async def model_swap(
    original_image: UploadFile = File(...),
    target_face_image: Optional[UploadFile] = File(None),  # Now Optional
    prompt: Optional[str] = Form(None),                    # Text-based swap
    face_reference_mode: str = Form("match_reference"),    # 'match_base' or 'match_reference'
    resolution: str = Form("1k"),                          # '1k', '2k', or '4k'
    generation_mode: Optional[str] = Form(None),           # 'fast', 'balanced', or 'quality'
    num_images: int = Form(1),                             # Must be between 1 and 4
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    orig_url = await process_upload(original_image)
    
    face_url = None
    if target_face_image:
        face_url = await process_upload(target_face_image)

    # 1. Dynamically build the payload based on what the user provided
    input_data = {
        "model_image": orig_url,
        "resolution": resolution,
        "num_images": num_images
    }
    
    if face_url:
        input_data["face_reference"] = face_url
        input_data["face_reference_mode"] = face_reference_mode
    if prompt:
        input_data["prompt"] = prompt
    if generation_mode:
        input_data["generation_mode"] = generation_mode

    # 2. Save tracking state to DB
    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.MODEL_SWAP,
        input_data=input_data  # Store the exact configuration used
    )
    db.add(db_job); db.commit(); db.refresh(db_job)

    try:
        # 3. Trigger FASHN with the exact keys defined in the documentation
        fashn_id = await trigger_generic_fashn_job(
            model_name="model-swap",
            inputs=input_data  # Directly pass the cleanly formatted dictionary
        )
        
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        
        return StandardResponse(
            status=True, 
            msg="Model Swap job started", 
            data={"job_id": db_job.id, "status": db_job.status.value}
        )
        
    except Exception as e:
        db.rollback()
        raise APIException(status_code=500, msg=str(e))

# ==========================================
# 3. IMAGE TO VIDEO (Static -> Motion)
# ==========================================
@router.post("/image-to-video", response_model=StandardResponse)
async def image_to_video(
    source_image: UploadFile = File(...),
    end_image: Optional[UploadFile] = File(None),          # Added: Optional final frame transition
    motion_prompt: Optional[str] = Form(None),             # Updated: FASHN recommends leaving empty
    duration: int = Form(5),                               # Added: 5 or 10
    resolution: str = Form("1080p"),                       # Added: 480p, 720p, or 1080p
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    source_url = await process_upload(source_image)
    
    # Process the end_image if the user uploaded one
    end_url = None
    if end_image:
        end_url = await process_upload(end_image)

    # 1. Dynamically build the payload based on provided inputs
    input_data = {
        "image": source_url,
        "duration": duration,
        "resolution": resolution
    }
    
    if end_url:
        input_data["end_image"] = end_url
    if motion_prompt:
        input_data["prompt"] = motion_prompt

    # 2. Save tracking state to DB
    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.IMAGE_TO_VIDEO,
        input_data=input_data  # Store exact configuration used
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    try:
        # 3. Trigger FASHN AI Engine
        fashn_id = await trigger_generic_fashn_job(
            model_name="image-to-video",
            inputs=input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        
        return StandardResponse(
            status=True, 
            msg="Video rendering started", 
            data={"job_id": db_job.id, "status": db_job.status.value}
        )
    except Exception as e:
        db.rollback()
        raise APIException(status_code=500, msg=str(e))
# ==========================================
# 4. BACKGROUND REPLACEMENT (2-Step Chain via Edit Model)
# ==========================================
async def process_background_change_chain(job_id: int, original_url: str, new_bg_prompt: str,reference_bg_url:Optional[str] = None):
    """Background worker: strips bg, then uses FASHN 'edit' model to apply new background."""
    db = SessionLocal()
    try:
        job = db.query(models.StudioJob).filter(models.StudioJob.id == job_id).first()
        if not job: return

        # Step 1: Remove Background
        logger.info(f"Job {job_id}: Stripping background...")
        bg_remove_id = await trigger_generic_fashn_job(
            model_name="background-remove",
            inputs={"image": original_url}
        )
        
        transparent_img_url = None
        while True:
            await asyncio.sleep(3)
            status, output = await check_vton_status(bg_remove_id)
            if status == "completed":
                transparent_img_url = output[0] if isinstance(output, list) else output
                break
            elif status == "failed":
                raise Exception("Background removal step failed.")

        # Step 2: Use 'edit' Model to Generate New Background behind subject
        logger.info(f"Job {job_id}: Applying edit model for background [{new_bg_prompt}]...")
        edit_prompt = f"add a realistic background behind the person: {new_bg_prompt}, seamless lighting and composition"
        
        inputs = {
            "image": transparent_img_url, 
            "prompt": edit_prompt
        }
        
        # If the user provided a real photo, pass it as the Image Context
        if reference_bg_url:
            inputs["image_context"] = reference_bg_url

        bg_gen_id = await trigger_generic_fashn_job(
            model_name="edit",  
            inputs=inputs
        )
        final_output = None
        while True:
            await asyncio.sleep(4)
            status, output = await check_vton_status(bg_gen_id)
            if status == "completed":
                # Ensure we store as a list for the JSON column
                final_output = output if isinstance(output, list) else [output]
                break
            elif status == "failed":
                raise Exception("Background generation edit step failed.")

        # Success - Save directly to JSON column
        job.result_urls = final_output
        job.status = models.JobStatus.COMPLETED
        db.commit()
        logger.info(f"Job {job_id}: Background replacement chain completed successfully.")

    except Exception as e:
        logger.error(f"Bg Change Error Job {job_id}: {str(e)}", exc_info=True)
        if 'job' in locals() and job:
            job.status = models.JobStatus.FAILED
            db.commit()
    finally:
        db.close()


@router.post("/change-background", response_model=StandardResponse)
async def change_background(
    background_tasks: BackgroundTasks,
    original_image: UploadFile = File(...),
    new_background_prompt: str = Form("In front of the Taj Mahal, cinematic lighting, 4k"),
    reference_bg_image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    orig_url = await process_upload(original_image)
    
    # Process the reference image if the user uploaded one
    ref_bg_url = None
    if reference_bg_image:
        ref_bg_url = await process_upload(reference_bg_image)
        
    # Save both URLs to the JSON column for tracking
    input_data = {"original_image": orig_url, "bg_prompt": new_background_prompt}
    if ref_bg_url:
        input_data["reference_bg_url"] = ref_bg_url

    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.BACKGROUND_CHANGE,
        input_data=input_data
    )
    db.add(db_job); db.commit(); db.refresh(db_job)

    # Dispatch the chain with the new reference URL
    background_tasks.add_task(process_background_change_chain, db_job.id, orig_url, new_background_prompt, ref_bg_url)

    return StandardResponse(
        status=True, 
        msg="Background replacement chain queued successfully.", 
        data={"job_id": db_job.id, "status": db_job.status.value}
    )
    
    
    
# ==========================================
# 5. MODEL CREATE (Generate AI Human from Text)
# ==========================================
@router.post("/model-create", response_model=StandardResponse)
async def model_create(
    prompt: str = Form(...),                                               # Required: Text description of the model
    image_reference: Optional[UploadFile] = File(None),                    # Optional: Composition/pose inspiration
    face_reference: Optional[UploadFile] = File(None),                     # Optional: Face identity lock
    face_reference_mode: str = Form("match_reference"),                    # 'match_base' or 'match_reference'
    aspect_ratio: Optional[str] = Form(None),                              # '1:1', '9:16', '16:9', etc.
    resolution: str = Form("1k"),                                          # '1k', '2k', or '4k'
    generation_mode: Optional[str] = Form(None),                           # 'fast', 'balanced', or 'quality'
    num_images: int = Form(1),                                             # 1 to 4
    output_format: str = Form("png"),                                      # 'png' or 'jpeg'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # 1. Base Payload with Required Parameters
    input_data = {
        "prompt": prompt,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }

    # 2. Process Optional Uploads and dynamically build payload
    if image_reference:
        input_data["image_reference"] = await process_upload(image_reference)
        
    if face_reference:
        input_data["face_reference"] = await process_upload(face_reference)
        # Only inject the mode if a face was actually provided
        input_data["face_reference_mode"] = face_reference_mode

    # 3. Append Optional Form Data
    if aspect_ratio:
        input_data["aspect_ratio"] = aspect_ratio
    if generation_mode:
        input_data["generation_mode"] = generation_mode

    # 4. Save tracking state to DB
    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.MODEL_CREATE,
        input_data=input_data  # Preserves all uploaded URLs and parameters
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    try:
        # 5. Trigger FASHN AI Engine
        fashn_id = await trigger_generic_fashn_job(
            model_name="model-create",
            inputs=input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        
        return StandardResponse(
            status=True, 
            msg="Model Create job started successfully.", 
            data={"job_id": db_job.id, "status": db_job.status.value}
        )
    except Exception as e:
        db.rollback()
        raise APIException(status_code=500, msg=f"AI Engine failed: {str(e)}")

# ==========================================
# 6. UNIFIED STATUS POLLING ENDPOINT
# ==========================================
@router.get("/status/{job_id}", response_model=StandardResponse)
async def get_studio_job_status(
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        db_job = db.query(models.StudioJob).filter(
            models.StudioJob.id == job_id, 
            models.StudioJob.user_id == current_user.id
        ).first()
        
        if not db_job:
            raise APIException(status_code=404, msg="Job not found.")

        # If it's a direct FASHN job (not a chained background task) and it's processing, update it
        if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id :
            fashn_status, output_data = await check_vton_status(db_job.fashn_job_id)

            if fashn_status == "completed":
                db_job.status = models.JobStatus.COMPLETED
                db_job.result_urls = output_data if isinstance(output_data, list) else [output_data]
                db.commit()
            elif fashn_status == "failed":
                db_job.status = models.JobStatus.FAILED
                db.commit()

       
        return StandardResponse(
            status=True, 
            msg="Job status retrieved.", 
            data={
                "id": db_job.id,
                "user_id": db_job.user_id,
                "job_type": db_job.job_type.value,  # Unpack Enum
                "status": db_job.status.value,      # Unpack Enum
                "fashn_job_id": db_job.fashn_job_id,
                "input_data": db_job.input_data,
                "result_urls": db_job.result_urls,
                "created_at": db_job.created_at.isoformat() if db_job.created_at else None
            }
        )

    except APIException:
        raise
    except Exception as e:
        logger.error(f"Error fetching studio status for Job {job_id}: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal error fetching status.")
    
    
    
    
# ==========================================
# 7. FACE TO MODEL (Headshot -> Try-on Avatar)
# ==========================================
@router.post("/face-to-model", response_model=StandardResponse)
async def face_to_model(
    face_image: UploadFile = File(...),                                    # Required: The cropped headshot/selfie
    prompt: Optional[str] = Form(None),                                    # Optional: Body shape guidance
    aspect_ratio: str = Form("2:3"),                                       # Supported: 1:1, 4:5, 3:4, 2:3, 9:16
    resolution: str = Form("1k"),                                          # '1k', '2k', or '4k'
    generation_mode: Optional[str] = Form(None),                           # 'fast', 'balanced', or 'quality'
    num_images: int = Form(1),                                             # 1 to 4
    output_format: str = Form("jpeg"),                                     # 'png' or 'jpeg' (FASHN defaults to jpeg here)
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # 1. Process Required Upload
    face_url = await process_upload(face_image)
    
    # 2. Base Payload with defaults mapped to documentation
    input_data = {
        "face_image": face_url,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }
    
    # 3. Append Optional Form Data cleanly
    if prompt:
        input_data["prompt"] = prompt
    if generation_mode:
        input_data["generation_mode"] = generation_mode

    # 4. Save tracking state to DB
    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.FACE_TO_MODEL,
        input_data=input_data
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    try:
        # 5. Trigger FASHN AI Engine
        fashn_id = await trigger_generic_fashn_job(
            model_name="face-to-model",
            inputs=input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        
        return StandardResponse(
            status=True, 
            msg="Face-to-Model avatar creation started successfully.", 
            data={"job_id": db_job.id, "status": db_job.status.value}
        )
    except Exception as e:
        db.rollback()
        raise APIException(status_code=500, msg=f"AI Engine failed: {str(e)}")