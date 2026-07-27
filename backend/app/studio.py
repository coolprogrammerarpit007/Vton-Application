import json
import logging
import asyncio
from typing import Optional                           
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Form, File, UploadFile, BackgroundTasks,Query

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


# ==============================================================================
# BACKGROUND WORKER: 2-Step Advanced Background Replacement Chain
# ==============================================================================
async def process_advanced_background_chain(
    job_id: int, 
    original_url: str, 
    harmonized_prompt: str,
    reference_bg_url: Optional[str],
    resolution: str,
    generation_mode: str
):
    """
    Executes the 2-step FASHN pipeline: 
    1. 'background-remove' to isolate the subject.
    2. 'edit' to generate the new background with global illumination.
    """
    db = SessionLocal()
    try:
        job = db.query(models.StudioJob).filter(models.StudioJob.id == job_id).first()
        if not job: 
            return

        job.status = models.JobStatus.PROCESSING
        db.commit()

        # ---------------------------------------------------------
        # STEP 1: Isolate the Subject (Background Remove API)
        # ---------------------------------------------------------
        logger.info(f"Job {job_id} [Step 1]: Stripping original background...")
        bg_remove_id = await trigger_generic_fashn_job(
            model_name="background-remove",
            inputs={"image": original_url}
        )
        
        transparent_img_url = None
        while True:
            await asyncio.sleep(3)
            status, output = await check_vton_status(bg_remove_id)
            if status == "completed":
                # API returns an array, extract the first URL
                transparent_img_url = output[0] if isinstance(output, list) else output
                break
            elif status == "failed":
                raise Exception("FASHN 'background-remove' step failed.")

        # ---------------------------------------------------------
        # STEP 2: Generate New Environment & Relight (Edit API)
        # ---------------------------------------------------------
        logger.info(f"Job {job_id} [Step 2]: Generating new background via Edit model...")
        
        edit_inputs = {
            "image": transparent_img_url, 
            "prompt": harmonized_prompt,
            "resolution": resolution,
            "generation_mode": generation_mode
        }
        
        # Pro-Tip: Inject the visual context if the user uploaded a reference image
        if reference_bg_url:
            edit_inputs["image_context"] = reference_bg_url

        bg_gen_id = await trigger_generic_fashn_job(
            model_name="edit",  
            inputs=edit_inputs
        )
        
        final_output_urls = None
        while True:
            await asyncio.sleep(5) # Edit takes longer, poll slightly slower
            status, output = await check_vton_status(bg_gen_id)
            if status == "completed":
                final_output_urls = output if isinstance(output, list) else [output]
                break
            elif status == "failed":
                raise Exception("FASHN 'edit' step failed.")

        # ---------------------------------------------------------
        # SUCCESS: Save outputs and mark completed
        # ---------------------------------------------------------
        job.result_urls = final_output_urls
        job.status = models.JobStatus.COMPLETED
        db.commit()
        logger.info(f"Job {job_id}: Advanced Background replacement completed successfully!")

    except Exception as e:
        logger.error(f"Background Change Chain Error on Job {job_id}: {str(e)}", exc_info=True)
        if 'job' in locals() and job:
            job.status = models.JobStatus.FAILED
            db.commit()
    finally:
        db.close()
    
    
# ==============================================================================
# API ENDPOINT: Request Background Change
# ==============================================================================
@router.post("/change-background", response_model=StandardResponse)
async def change_background(
    background_tasks: BackgroundTasks,
    original_image: UploadFile = File(...),                                      # Required: The original photo (e.g., person at Taj Mahal)
    new_background_prompt: str = Form(...),                                      # Required: Description of new location (e.g., "The Great Wall of China")
    reference_bg_image: Optional[UploadFile] = File(None),                       # Optional: Provide a specific photo of the Great Wall to use as context
    resolution: str = Form("2k"),                                                # Default to 2k for better realism
    generation_mode: str = Form("quality"),                                      # Pro-tip: Force 'quality' mode for best lighting/shadows
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # 1. Save and resolve the original image
    orig_url = await process_upload(original_image)
    
    # 2. Process the reference context image if the user uploaded one
    ref_bg_url = None
    if reference_bg_image:
        ref_bg_url = await process_upload(reference_bg_image)
        
    # 3. --- PRO-TIP: PROMPT HARMONIZATION ---
    # We intercept the user's basic prompt and inject professional lighting instructions.
    # This prevents the "photoshopped" look by forcing the AI to bleed environmental light onto the subject.
    harmonized_prompt = (
        f"{new_background_prompt.strip()}. "
        "The subject is fully immersed in this environment. Global illumination, "
        "matching color grading, environmental light bleeding onto the subject, "
        "seamless shadows at the feet, highly realistic photographic composite."
    )
        
    # 4. Save tracking state to database
    input_data = {
        "original_image": orig_url, 
        "user_prompt": new_background_prompt,
        "harmonized_prompt": harmonized_prompt,
        "resolution": resolution,
        "generation_mode": generation_mode
    }
    if ref_bg_url:
        input_data["image_context"] = ref_bg_url

    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.BACKGROUND_CHANGE,
        input_data=input_data
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    # 5. Dispatch the asynchronous background task chain
    background_tasks.add_task(
        process_advanced_background_chain, 
        job_id=db_job.id, 
        original_url=orig_url, 
        harmonized_prompt=harmonized_prompt, 
        reference_bg_url=ref_bg_url,
        resolution=resolution,
        generation_mode=generation_mode
    )

    return StandardResponse(
        status=True, 
        msg="Advanced background replacement queued successfully. The AI is isolating the subject and rendering the new environment.", 
        data={"job_id": db_job.id, "status": db_job.status.value}
    )
    
# ==========================================
# 5. MODEL CREATE (Generate AI Human from Text)
# ==========================================
@router.post("/model-create", response_model=StandardResponse)
async def model_create(
    # --- DYNAMIC PROMPT ATTRIBUTES (From Frontend UI) ---
    age: Optional[str] = Form("25"),
    gender: Optional[str] = Form("Female"),
    ethnicity: Optional[str] = Form("Caucasian"),
    build_type: Optional[str] = Form("Slim"),
    
    hair_length: Optional[str] = Form("N/A"),
    hair_color: Optional[str] = Form("Dark brown"),
    hair_type: Optional[str] = Form("Wavy"),
    hair_style: Optional[str] = Form("Long flowing"),
    
    eye_color: Optional[str] = Form("Deep brown"),
    face_shape: Optional[str] = Form("Oval"),
    jawline: Optional[str] = Form("Soft"),
    eyebrow: Optional[str] = Form("Arched"),
    face_expression: Optional[str] = Form("Calm"),
    skin_color: Optional[str] = Form("Fair"),
    
    # --- STANDARD PARAMS ---
    custom_prompt: Optional[str] = Form(None),                               # Fallback for manual override
    image_reference: Optional[UploadFile] = File(None),                      # Optional: Composition/pose inspiration
    face_reference: Optional[UploadFile] = File(None),                       # Optional: Face identity lock
    face_reference_mode: str = Form("match_reference"),                      # 'match_base' or 'match_reference'
    aspect_ratio: Optional[str] = Form(None),                                # '1:1', '9:16', '16:9', etc.
    resolution: str = Form("1k"),                                            # '1k', '2k', or '4k'
    generation_mode: Optional[str] = Form(None),                             # 'fast', 'balanced', or 'quality'
    num_images: int = Form(1),                                               # 1 to 4
    output_format: str = Form("png"),                                        # 'png' or 'jpeg'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    
    # 1. Synthesize the Master Prompt Dynamically
    if custom_prompt and custom_prompt.strip():
        final_prompt = custom_prompt.strip()
    
    else:
        # Build the prompt strictly from the provided UI attributes
        final_prompt = (
            f"A highly detailed, professional studio portrait of a {age}-year-old {ethnicity} {gender}, "
            f"with a {build_type} body build. "
            f"The model has {skin_color} skin, an {face_shape} face shape, a {jawline} jawline, "
            f"and {eyebrow} eyebrows. Their eyes are {eye_color}, showing a {face_expression} expression. "
            f"Hair details: {hair_length}, {hair_color}, {hair_type} texture, styled as {hair_style}. "
            f"Fashion photography, {resolution}, photorealistic, cinematic lighting."
        )
        
    # Payload strictly for FASHN API
    fashn_input_data = {
        "prompt": final_prompt,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }
    
    
   # Payload for your local database (includes UI attributes)
    db_input_data = {
        "prompt": final_prompt,
        "attributes": {
            "age": age,
            "gender": gender,
            "ethnicity": ethnicity,
            "build_type": build_type,
            "hair": {
                "length": hair_length,
                "color": hair_color,
                "type": hair_type,
                "style": hair_style
            },
            "face": {
                "eye_color": eye_color,
                "face_shape": face_shape,
                "jawline": jawline,
                "eyebrow": eyebrow,
                "face_expression": face_expression,
                "skin_color": skin_color
            }
        },
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }

    # 3. Process Optional Uploads
    if image_reference:
        img_ref_url = await process_upload(image_reference)
        fashn_input_data["image_reference"] = img_ref_url
        db_input_data["image_reference"] = img_ref_url
        
    if face_reference:
        face_ref_url = await process_upload(face_reference)
        fashn_input_data["face_reference"] = face_ref_url
        db_input_data["face_reference"] = face_ref_url
        
        fashn_input_data["face_reference_mode"] = face_reference_mode
        db_input_data["face_reference_mode"] = face_reference_mode

    # 4. Append Optional Form Data
    if aspect_ratio:
        fashn_input_data["aspect_ratio"] = aspect_ratio
        db_input_data["aspect_ratio"] = aspect_ratio
    if generation_mode:
        fashn_input_data["generation_mode"] = generation_mode
        db_input_data["generation_mode"] = generation_mode
    
   # 5. Save tracking state to DB
    db_job = models.StudioJob(
        user_id=current_user.id,
        job_type=models.StudioJobType.MODEL_CREATE,
        input_data=db_input_data, # <--- Use the DB dictionary here
        is_active=True
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    try:
        # 5. Trigger FASHN AI Engine
        fashn_id = await trigger_generic_fashn_job(
            model_name="model-create",
            inputs=fashn_input_data
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
# GET USER MODELS (Retrieve AI Human Generation History)
# ==========================================
@router.get("/my-models", response_model=StandardResponse)
async def get_user_models(
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Max number of records to return"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # 1. Query the database for jobs matching the user and job type
        base_query = db.query(models.StudioJob).filter(
            models.StudioJob.user_id == current_user.id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED,
            models.StudioJob.is_active == True
        )

        # 2. Get the total count for frontend pagination logic
        total_count = base_query.count()

        # 3. Fetch the paginated records, ordering by newest first
        # Note: If your StudioJob model has a 'created_at' column, use models.StudioJob.created_at.desc() instead
        jobs = base_query.order_by(models.StudioJob.id.desc()).offset(skip).limit(limit).all()

        # 4. Serialize the data to extract the generated image URLs
        formatted_jobs = []
        for job in jobs:
            
            # Extract prompt safely from input_data JSON column
            prompt_text = ""
            if job.input_data and isinstance(job.input_data, dict):
                prompt_text = job.input_data.get("prompt", "")
            
            # Extract generated images directly from the result_urls column
            generated_images = []
            if job.result_urls:
                # If your DB returns a string instead of a native list/JSON, use json.loads(job.result_urls)
                generated_images = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)
                
            # Handle enum serialization for status
            job_status = job.status.value if hasattr(job.status, 'value') else job.status

            formatted_jobs.append({
                "job_id": job.id,
                "fashn_job_id": job.fashn_job_id,
                "status": job_status,
                "prompt": prompt_text,
                "generated_image_urls": generated_images,  # Mapped directly to your DB column
                "created_at": job.created_at,
                "updated_at": job.updated_at
            })

        # 5. Format the response
        return StandardResponse(
            status=True,
            msg="User models retrieved successfully.",
            data={
                "total": total_count,
                "skip": skip,
                "limit": limit,
                "jobs": formatted_jobs
            }
        )
        

    except Exception as e:
        # Catch and handle database or unexpected errors consistently
        raise APIException(status_code=500, msg=f"Failed to fetch models: {str(e)}")

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
                urls = output_data if isinstance(output_data, list) else [output_data]
                db_job.result_urls = urls
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
                "job_type": db_job.job_type.value if hasattr(db_job.job_type, 'value') else db_job.job_type,
                "status": db_job.status.value if hasattr(db_job.status, 'value') else db_job.status,
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