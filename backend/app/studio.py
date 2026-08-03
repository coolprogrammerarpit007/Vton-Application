import json
import logging
import asyncio
from typing import Optional                           
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Form, File, UploadFile, BackgroundTasks,Query,Path

from . import models
from .database import get_db, SessionLocal
from .auth import get_current_user
from .utils import save_upload_file
from .fashn_service import trigger_generic_fashn_job, check_vton_status
from .schemas import StandardResponse
from .exceptions import APIException


from sqlalchemy import func




logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/studio", tags=["AI Creative Studio"])
base_url = "https://vton-backend.microcrm.in"



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
    # 1. Save and resolve the original image (with exception handling)
    try:
        orig_url = await process_upload(original_image)
        
        # 2. Process the reference context image if the user uploaded one
        ref_bg_url = None
        if reference_bg_image:
            ref_bg_url = await process_upload(reference_bg_image)
    except Exception as e:
        raise APIException(status_code=400, msg=f"Failed to process image uploads: {str(e)}")
    
    
    # 3. --- PRO-TIP: DYNAMIC PROMPT HARMONIZATION & SHADOW CONTROL ---
    try:
        prompt_lower = new_background_prompt.lower()
        
        # Check if the user is asking for a night, dark, or evening scene
        is_night_scene = any(word in prompt_lower for word in ["night", "dark", "evening", "midnight", "dusk"])
        
        # Dynamically adjust lighting and shadow instructions based on the time of day
        if is_night_scene:
            lighting_instructions = (
                "Nighttime environmental lighting, subtle ambient occlusion, "
                "diffuse ambient light, faint and blurred ground contact shadows, "
                "no harsh directional shadows, absolutely no mirror reflections on the ground."
            )
        else:
            lighting_instructions = (
                "Natural global illumination, matching color grading, "
                "soft and realistic contact shadows at the feet, diffuse lighting, "
                "absolutely no glossy or mirror-like reflections on the ground."
            )

        harmonized_prompt = (
            f"{new_background_prompt.strip()}. "
            f"The subject is perfectly integrated into this scene. "
            f"{lighting_instructions} Highly realistic photographic composite, natural depth of field."
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

    except Exception as e:
        # Rollback the database transaction if anything fails during DB save
        db.rollback()
        raise APIException(status_code=500, msg=f"Database error while saving job: {str(e)}")

    # 5. Dispatch the asynchronous background task chain
    try:
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
    except Exception as e:
        raise APIException(status_code=500, msg=f"Failed to queue background generation task: {str(e)}")
    
# ==========================================
# 5. MODEL CREATE (Generate AI Human from Text)
# ==========================================

# ==========================================
# SMART PROMPT SANITIZER HELPERS
# ==========================================

def build_smart_gender_anchor(age_str: str, gender_str: str, ethnicity_str: str) -> tuple[str, str]:
    """Enforces non-negotiable age, gender, and ethnicity anchors."""
    try:
        age_int = int(age_str)
    except (ValueError, TypeError):
        age_int = 25
        
    g_lower = (gender_str or "female").strip().lower()
    e_str = (ethnicity_str or "Caucasian").strip()
    
    if g_lower == "male":
        if age_int < 18:
            noun = "male boy"
        elif age_int <= 25:
            noun = "young male man"
        else:
            noun = "male man"
        gender_weight = "(masculine male features, handsome male face:1.4)"
        
    elif g_lower == "female":
        if age_int < 18:
            noun = "female girl"
        elif age_int <= 25:
            noun = "young female woman"
        else:
            noun = "female woman"
        gender_weight = "(feminine female features, beautiful female face:1.4)"
        
    else:
        noun = f"{gender_str} person"
        gender_weight = ""
        
    anchor = f"a {age_int}-year-old {e_str} {noun}"
    return anchor, gender_weight


def sanitize_hair_details(hair_length: str, hair_color: str, hair_type: str, hair_style: str) -> str:
    """Reconciles contradictory hair attributes (e.g., Short length + Long style)."""
    length = (hair_length or "").strip()
    color = (hair_color or "").strip()
    htype = (hair_type or "").strip()
    style = (hair_style or "").strip()
    
    # Resolve Short vs Long conflicts
    if length.lower() == "short" and "long" in style.lower():
        style = style.lower().replace("long", "").strip().capitalize()
    elif length.lower() in ["long", "waist-length"] and "short" in style.lower():
        style = style.lower().replace("short", "").strip().capitalize()

    parts = []
    if length and length.upper() != "N/A":
        parts.append(length)
    if color and color.upper() != "N/A":
        parts.append(color)
    if htype and htype.upper() != "N/A":
        parts.append(f"{htype} texture")
    
    base_hair = ", ".join(parts) if parts else "neatly groomed hair"
    
    if style and style.upper() != "N/A":
        return f"{base_hair}, styled as {style}"
    return base_hair


def sanitize_outfit_for_gender(outfit: str, gender: str) -> str:
    """Prevents female garments from overpowering a male model generation."""
    outfit_clean = (outfit or "").strip()
    gender_lower = (gender or "").strip().lower()
    
    if not outfit_clean:
        return "wearing a casual top"

    # Preposition formatting
    prefixes = ("wearing", "dressed in", "in ", "clad in")
    if not any(outfit_clean.lower().startswith(p) for p in prefixes):
        first_word = outfit_clean.split()[0].lower()
        if first_word in ["a", "an", "the"]:
            outfit_clean = f"wearing {outfit_clean}"
        else:
            outfit_clean = f"wearing a {outfit_clean}"

    # Handle male + female garment contradiction
    if gender_lower == "male":
        female_garments = ["dress", "skirt", "gown", "blouse", "bikini", "bra", "crop top", "frock", "saree", "lehenga"]
        if any(fg in outfit_clean.lower() for fg in female_garments):
            # Convert female garments to male equivalents to prevent gender distortion
            outfit_clean = outfit_clean.lower()
            outfit_clean = outfit_clean.replace("dress", "casual summer outfit")
            outfit_clean = outfit_clean.replace("skirt", "shorts")
            outfit_clean = outfit_clean.replace("gown", "tailored suit")
            outfit_clean = outfit_clean.replace("blouse", "shirt")
            outfit_clean = outfit_clean.replace("bikini", "swim trunks")
            outfit_clean = outfit_clean.replace("frock", "shirt")
            outfit_clean = f"{outfit_clean.capitalize()} (masculine male apparel)"

    return outfit_clean


def sanitize_background(setting: str) -> str:
    """Fixes incomplete background/setting grammar."""
    setting_clean = (setting or "").strip()
    if not setting_clean:
        return "standing in a minimalist photography studio setting"
        
    prefixes = ("standing", "sitting", "in ", "at ", "against", "in front of", "located in")
    if not any(setting_clean.lower().startswith(p) for p in prefixes):
        return f"standing in a {setting_clean}"
    return setting_clean


# ==========================================

@router.post("/model-create", response_model=StandardResponse)
async def model_create(
    # --- DYNAMIC PROMPT ATTRIBUTES (From Frontend UI) ---
    model_name:str = Form(...),
    age: Optional[str] = Form("25"),
    gender: Optional[str] = Form("Female"),
    ethnicity: Optional[str] = Form("Caucasian"),
    build_type: Optional[str] = Form("Slim"),
    
    hair_length: Optional[str] = Form("N/A"),
    hair_color: Optional[str] = Form("Dark brown"),
    hair_type: Optional[str] = Form("Wavy"),
    hair_style: Optional[str] = Form("Long flowing"),
    
    # Male Beard Type
    beard_type:Optional[str] = Form(""),
    
    eye_color: Optional[str] = Form("Deep brown"),
    face_shape: Optional[str] = Form("Oval"),
    jawline: Optional[str] = Form("Soft"),
    eyebrow: Optional[str] = Form("Arched"),
    face_expression: Optional[str] = Form("Calm"),
    skin_color: Optional[str] = Form("Fair"),
    
    
    # --- NEW DYNAMIC VARIABLES (Add these to your API endpoint/form) ---
    camera_framing: Optional[str] = Form("Medium shot, from waist up"), # e.g., "Full body shot", "Close-up portrait", "Three-quarter body photo"
    camera_angle: Optional[str] = Form("Eye-level angle"), # e.g., "Low side angle", "High angle"
    outfit_description: Optional[str] = Form("wearing a black ribbed tank top"),
    background_setting: Optional[str] = Form("standing in front of a modern gray tiled wall"),
    
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
       # A. Non-negotiable Age & Gender Anchor
        subject_anchor, gender_weight = build_smart_gender_anchor(age, gender, ethnicity)
        
        # B. Sanitize Hair Details
        clean_hair = sanitize_hair_details(hair_length, hair_color, hair_type, hair_style)
        
        # C. Sanitize Outfit (Prevents gender-garment mismatch)
        clean_outfit = sanitize_outfit_for_gender(outfit_description, gender)
        
        # D. Sanitize Background
        clean_background = sanitize_background(background_setting)
        
        # E. Construct Master Prompt
        final_prompt = (
            # 1. Framing and Camera Angle
            f"(Close-up {camera_framing}:1.4) from a {camera_angle}, "
            
            # 2. Non-Negotiable Subject Anchor & Gender Weighting
            f"of {subject_anchor} {gender_weight} with a {build_type} body build. "
            f"The model has {skin_color} skin, an {face_shape} face shape, a {jawline} jawline, "
            f"and {eyebrow} eyebrows. Their eyes are {eye_color}, showing a {face_expression} expression. "
            f"Hair details: {clean_hair}. "
            
            # 3. Outfit & Environment
            f"The model is {clean_outfit}. "
            f"The setting is {clean_background}. "
            
            # 4. Strict Framing Enforcement
            f"Fashion photography, (strict waist-up portrait:1.5), (lower body is strictly out of frame:1.5), "
            f"{resolution}, photorealistic, cinematic lighting, shot on 85mm lens, high-definition."
        )

    # # --- HARDCODED NEGATIVE PROMPT ---
    # # Prevents rendering anything below the waist, ensuring a tighter crop
    # backend_negative_prompt = (
    #     "full body, full length, legs, feet, shoes, knees, wide shot, long shot, "
    #     "distance shot, standing, pants, trousers, skirt, lower body"
    # )

    # 2. Payload strictly for FASHN API
    fashn_input_data = {
        "prompt": final_prompt,
        # "negative_prompt": backend_negative_prompt, # Inject negative prompt here
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }
        
    
    
    
   # Payload for your local database (includes UI attributes)
    db_input_data = {
        "prompt": final_prompt,
        "attributes": {
            "model_name":model_name,
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

# Original Working Code for getting models


# GET USER MODELS (Retrieve AI Human Generation History)
# ==========================================
# @router.get("/my-models", response_model=StandardResponse)
# async def get_user_models(
#     skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
#     limit: int = Query(20, ge=1, le=100, description="Max number of records to return"),
#     db: Session = Depends(get_db),
#     current_user: models.User = Depends(get_current_user)
# ):
#     try:
#         # 1. Query the database for jobs matching the user and job type
#         base_query = db.query(models.StudioJob).filter(
#             models.StudioJob.user_id == current_user.id,
#             models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
#             models.StudioJob.status == models.JobStatus.COMPLETED,
#             models.StudioJob.is_active == True
#         )

#         # 2. Get the total count for frontend pagination logic
#         total_count = base_query.count()

#         # 3. Fetch the paginated records, ordering by newest first
#         # Note: If your StudioJob model has a 'created_at' column, use models.StudioJob.created_at.desc() instead
#         jobs = base_query.order_by(models.StudioJob.id.desc()).offset(skip).limit(limit).all()

#         # 4. Serialize the data to extract the generated image URLs
#         formatted_jobs = []
#         for job in jobs:
            
#             # Extract prompt safely from input_data JSON column
#             prompt_text = ""
#             if job.input_data and isinstance(job.input_data, dict):
#                 prompt_text = job.input_data.get("prompt", "")
            
#             # Extract generated images directly from the result_urls column
#             generated_images = []
#             if job.result_urls:
#                 # If your DB returns a string instead of a native list/JSON, use json.loads(job.result_urls)
#                 generated_images = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)
                
#             # Handle enum serialization for status
#             job_status = job.status.value if hasattr(job.status, 'value') else job.status

#             formatted_jobs.append({
#                 "job_id": job.id,
#                 "fashn_job_id": job.fashn_job_id,
#                 "status": job_status,
#                 "prompt": prompt_text,
#                 "generated_image_urls": generated_images,  # Mapped directly to your DB column
#                 "created_at": job.created_at,
#                 "updated_at": job.updated_at
#             })

#         # 5. Format the response
#         return StandardResponse(
#             status=True,
#             msg="User models retrieved successfully.",
#             data={
#                 "total": total_count,
#                 "skip": skip,
#                 "limit": limit,
#                 "jobs": formatted_jobs
#             }
#         )
        

#     except Exception as e:
        # Catch and handle database or unexpected errors consistently
        raise APIException(status_code=500, msg=f"Failed to fetch models: {str(e)}")

# ************************** End of Original Working code *******************

# ************** Updated My-Models Code *************************

# ==========================================
# GET USER MODELS (Retrieve AI Human Generation History)
# ==========================================
@router.get("/my-models", response_model=StandardResponse)
async def get_user_models(
    gender: Optional[str] = Query(None, description="Filter models by gender (e.g., 'Male' or 'Female')"), # <-- NEW PARAMETER
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Max number of records to return"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # 1. Base query for the current user's completed models
        base_query = db.query(models.StudioJob).filter(
            models.StudioJob.user_id == current_user.id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED,
            models.StudioJob.is_active == True
        )

        # 2. NEW: Filter by Gender if provided
        if gender:
            # MySQL / MariaDB compatible JSON extraction
            # We use lower() on both sides to make it case-insensitive (e.g., 'female' matches 'Female')
            base_query = base_query.filter(
                func.lower(func.json_unquote(func.json_extract(models.StudioJob.input_data, '$.attributes.gender'))) == gender.lower()
            )

        # 3. Get the total count for frontend pagination logic
        total_count = base_query.count()

        # 4. Fetch the paginated records, ordering by newest first
        jobs = base_query.order_by(models.StudioJob.id.desc()).offset(skip).limit(limit).all()

        # 5. Serialize the data
        formatted_jobs = []
        for job in jobs:
            
            # Safely parse input_data whether it's stored as a native dict (JSON column) or string
            input_dict = job.input_data if isinstance(job.input_data, dict) else (json.loads(job.input_data) if job.input_data else {})
            
            prompt_text = input_dict.get("prompt", "")
            
            # NEW: Extract the full attributes dictionary (age, hair, face, etc.)
            attributes = input_dict.get("attributes", {}) 
            
            # Extract generated images
            generated_images = []
            if job.result_urls:
                generated_images = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)
                
            # Handle enum serialization for status
            job_status = job.status.value if hasattr(job.status, 'value') else job.status

            formatted_jobs.append({
                "job_id": job.id,
                "fashn_job_id": job.fashn_job_id,
                "status": job_status,
                "prompt": prompt_text,
                "attributes": attributes,  # <-- NEW: Appended to the response for the frontend
                "generated_image_urls": generated_images, 
                "created_at": job.created_at,
                "updated_at": job.updated_at
            })

        # 6. Format the response
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
        raise APIException(status_code=500, msg=f"Failed to fetch models: {str(e)}")
    
    
    
    
# ==========================================
# GET SINGLE USER MODEL (Retrieve Specific AI Human)
# ==========================================
@router.get("/model-detail/{job_id}", response_model=StandardResponse)
async def get_model_detail(
    job_id: int = Path(..., description="The unique ID of the model job"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # 1. Fetch the specific job ensuring it belongs to the user and meets all criteria
        job = db.query(models.StudioJob).filter(
            models.StudioJob.id == job_id,
            models.StudioJob.user_id == current_user.id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED,
            models.StudioJob.is_active == True
        ).first()

        # 2. Return 404 if not found or unauthorized
        if not job:
            raise APIException(status_code=404, msg="Model not found or is no longer active.")

        # 3. Safely parse input_data
        input_dict = job.input_data if isinstance(job.input_data, dict) else (json.loads(job.input_data) if job.input_data else {})
        prompt_text = input_dict.get("prompt", "")
        attributes = input_dict.get("attributes", {}) 
        
        # 4. Extract generated images
        generated_images = []
        if job.result_urls:
            generated_images = job.result_urls[0] if isinstance(job.result_urls, list) else json.loads(job.result_urls)
            
        # 5. Handle enum serialization for status
        job_status = job.status.value if hasattr(job.status, 'value') else job.status

        # 6. Serialize the data into the exact format used in the list API
        formatted_job = {
            "job_id": job.id,
            "fashn_job_id": job.fashn_job_id,
            "status": job_status,
            "prompt": prompt_text,
            "attributes": attributes,
            "generated_image_urls": generated_images, 
            "created_at": job.created_at,
            "updated_at": job.updated_at
        }

        # 7. Format and return the response
        return StandardResponse(
            status=True,
            msg="Model detail retrieved successfully.",
            data=formatted_job
        )
        
    except APIException:
        raise
    except Exception as e:
        raise APIException(status_code=500, msg=f"Failed to fetch model details: {str(e)}")


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