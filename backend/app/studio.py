import json
import logging
import asyncio
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Form, File, UploadFile, Query, Path, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from . import models
from .database import get_db, SessionLocal
from .auth import get_current_user
from .utils import save_upload_file, download_and_save_remote_image
from .fashn_service import trigger_generic_fashn_job, check_vton_status, ensure_fashn_credits_available
from .schemas import StandardResponse
from .exceptions import APIException
from .config import settings
from .gatekeeper import PlanGatekeeper, SubscriptionTransactionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/studio", tags=["AI Creative Studio"])

base_url = settings.BACKEND_URL.rstrip("/")


def validate_image(file: UploadFile):
    if not file.content_type.startswith("image/"):
        raise APIException(status_code=200, msg="Invalid file format. Must be an image.")


async def process_upload(file: UploadFile) -> str:
    validate_image(file)
    filename = save_upload_file(file)
    return f"{base_url}/static_uploads/{filename}"


def resolve_model_image_url(
    db: Session,
    user_id: int,
    generated_model_job_id: Optional[int] = None,
    upload_file: Optional[UploadFile] = None
) -> str:
    if generated_model_job_id:
        studio_job = db.query(models.StudioJob).filter(
            models.StudioJob.id == generated_model_job_id,
            models.StudioJob.user_id == user_id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED
        ).first()

        if not studio_job or not studio_job.result_urls:
            raise APIException(status_code=200, msg="Selected generated model not found or job has not completed yet.")

        urls = studio_job.result_urls if isinstance(studio_job.result_urls, list) else json.loads(studio_job.result_urls)
        if not urls:
            raise APIException(status_code=200, msg="No images found in the selected generated model.")

        return urls[0]

    elif upload_file:
        validate_image(upload_file)
        filename = save_upload_file(upload_file)
        return f"{base_url}/static_uploads/{filename}"

    raise APIException(status_code=200, msg="Must provide either an uploaded image or a valid generated_model_job_id.")


# ==========================================
# 1. PRODUCT TO MODEL
# ==========================================
@router.post("/product-to-model", response_model=StandardResponse)
async def product_to_model(
    garment_image: UploadFile = File(...),
    generated_model_job_id: Optional[int] = Form(None),
    image_prompt: Optional[UploadFile] = File(None),
    face_reference: Optional[UploadFile] = File(None),
    background_reference: Optional[UploadFile] = File(None),
    prompt: Optional[str] = Form(None),
    face_reference_mode: str = Form("match_reference"),
    aspect_ratio: Optional[str] = Form(None),
    resolution: str = Form("1k"),
    generation_mode: Optional[str] = Form(None),
    num_images: int = Form(1),
    output_format: str = Form("png"),
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="product_to_model"))
):
    await ensure_fashn_credits_available(min_required=1.0)

    if resolution == "4k" and subscription.plan_snapshot.get("image_quality", "2k") == "2k":
        raise APIException(status_code=200, msg="4K render quality requires the Gold or Platinum plan.")

    cost = SubscriptionTransactionManager.calculate_cost(
        "photoshoot_image", subscription.plan_snapshot, {"image_quality": resolution}
    )

    garment_url = await process_upload(garment_image)

    input_data = {
        "product_image": garment_url,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }

    if generated_model_job_id:
        model_url = resolve_model_image_url(db, subscription.user_id, generated_model_job_id=generated_model_job_id)
        input_data["image_prompt"] = model_url
    elif image_prompt:
        input_data["image_prompt"] = await process_upload(image_prompt)

    if background_reference:
        input_data["background_reference"] = await process_upload(background_reference)

    if face_reference:
        input_data["face_reference"] = await process_upload(face_reference)
        input_data["face_reference_mode"] = face_reference_mode

    if prompt:
        input_data["prompt"] = prompt
    if aspect_ratio:
        input_data["aspect_ratio"] = aspect_ratio
    if generation_mode:
        input_data["generation_mode"] = generation_mode

    db_job = models.StudioJob(
        user_id=subscription.user_id,
        job_type=models.StudioJobType.PRODUCT_TO_MODEL,
        input_data=input_data
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    SubscriptionTransactionManager.deduct_resources(
        db, subscription, cost, "product_to_model", reference_id=db_job.id
    )

    try:
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
            data={"job_id": db_job.id, "status": db_job.status.value, "credits_deducted": cost}
        )

    except Exception as e:
        db.rollback()
        SubscriptionTransactionManager.refund_resources(
            db, subscription, cost, "product_to_model", reference_id=db_job.id, reason=str(e)
        )
        raise APIException(status_code=200, msg=f"AI Engine failed: {str(e)}")


# ==========================================
# 2. MODEL SWAP
# ==========================================
@router.post("/model-swap", response_model=StandardResponse)
async def model_swap(
    # --- Original Model (Body) ---
    original_image: Optional[UploadFile] = File(None),
    generated_model_job_id: Optional[int] = Form(None),
    
    # --- Target Face (The new face to swap in) ---
    target_face_image: Optional[UploadFile] = File(None),
    target_model_job_id: Optional[int] = Form(None),  # FIXED: Changed from UploadFile/File to int/Form
    
    # --- Parameters ---
    prompt: Optional[str] = Form(None),
    face_reference_mode: str = Form("match_reference"),
    resolution: str = Form("1k"),
    generation_mode: Optional[str] = Form(None),
    num_images: int = Form(1),
    
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="model_swap"))
):
    await ensure_fashn_credits_available(min_required=1.0)
    cost = SubscriptionTransactionManager.calculate_cost("model_swap", subscription.plan_snapshot)
    
    # 1. Resolve Original Model (Body)
    orig_url = resolve_model_image_url(db, subscription.user_id, generated_model_job_id, original_image)
    
    # 2. Resolve Target Face (New Face) safely using the existing helper
    face_url = None
    if target_model_job_id or target_face_image:
        face_url = resolve_model_image_url(db, subscription.user_id, target_model_job_id, target_face_image)

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

    db_job = models.StudioJob(
        user_id=subscription.user_id, job_type=models.StudioJobType.MODEL_SWAP, input_data=input_data
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    SubscriptionTransactionManager.deduct_resources(db, subscription, cost, "model_swap", reference_id=db_job.id)

    try:
        fashn_id = await trigger_generic_fashn_job(
            model_name="model-swap",
            inputs=input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()

        return StandardResponse(status=True, msg="Model Swap job started", data={"job_id": db_job.id, "credits_deducted": cost})

    except Exception as e:
        db.rollback()
        SubscriptionTransactionManager.refund_resources(db, subscription, cost, "model_swap", reference_id=db_job.id, reason=str(e))
        raise APIException(status_code=200, msg=str(e))


# ==========================================
# 3. IMAGE TO VIDEO
# ==========================================
@router.post("/image-to-video", response_model=StandardResponse)
async def image_to_video(
    source_image: Optional[UploadFile] = File(None),
    generated_model_job_id: Optional[int] = Form(None),
    end_image: Optional[UploadFile] = File(None),
    motion_prompt: Optional[str] = Form(None),
    duration: int = Form(5),
    resolution: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper(resource_key=models.ResourceKey.IMAGE_TO_VIDEO))
):
    await ensure_fashn_credits_available(min_required=1.0)

    if resolution and not resolution.endswith("p"):
        resolution = f"{resolution}p"

    if not resolution:
        resolution = subscription.plan_snapshot.get("video_quality", "480p")

    if resolution == "1080p" and "platinum" not in subscription.plan_snapshot.get("plan_name", "").lower():
        raise APIException(status_code=200, msg="1080p Pro video rendering is exclusively available on the Platinum plan.")

    quality_map = {"480p": 1, "720p": 2, "1080p": 3}
    user_max = quality_map.get(subscription.plan_snapshot.get("video_quality", "480p"), 1)
    req_min = quality_map.get(resolution, 1)

    if req_min > user_max:
        raise APIException(status_code=200, msg=f"Your plan is limited to {subscription.plan_snapshot.get('video_quality')} video exports.")

    cost = SubscriptionTransactionManager.calculate_cost("video_generation", subscription.plan_snapshot, {"resolution": resolution})
    source_url = resolve_model_image_url(db, subscription.user_id, generated_model_job_id, source_image)

    end_url = None
    if end_image:
        end_url = await process_upload(end_image)

    input_data = {
        "image": source_url,
        "duration": duration,
        "resolution": resolution
    }

    if end_url:
        input_data["end_image"] = end_url
    if motion_prompt:
        input_data["prompt"] = motion_prompt

    db_job = models.StudioJob(
        user_id=subscription.user_id, job_type=models.StudioJobType.IMAGE_TO_VIDEO, input_data=input_data
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    SubscriptionTransactionManager.deduct_resources(
        db, subscription, cost, "image_to_video", quota_key=models.ResourceKey.IMAGE_TO_VIDEO, reference_id=db_job.id
    )

    try:
        fashn_id = await trigger_generic_fashn_job(model_name="image-to-video", inputs=input_data)
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        return StandardResponse(status=True, msg="Video rendering started", data={"job_id": db_job.id, "credits_deducted": cost})
    except Exception as e:
        db.rollback()
        SubscriptionTransactionManager.refund_resources(
            db, subscription, cost, "image_to_video", quota_key=models.ResourceKey.IMAGE_TO_VIDEO, reference_id=db_job.id, reason=str(e)
        )
        raise APIException(status_code=200, msg=str(e))


# ==============================================================================
# BACKGROUND WORKER: Advanced Background Replacement Chain
# ==============================================================================
async def process_advanced_background_chain(
    job_id: int, 
    user_id: int,
    cost: int,
    original_url: str, 
    harmonized_prompt: str,
    reference_bg_url: Optional[str],
    resolution: str,
    generation_mode: str
):
    db = SessionLocal()
    try:
        job = db.query(models.StudioJob).filter(models.StudioJob.id == job_id).first()
        if not job: 
            return

        job.status = models.JobStatus.PROCESSING
        db.commit()

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
                raise Exception("FASHN 'background-remove' step failed.")

        edit_inputs = {
            "image": transparent_img_url, 
            "prompt": harmonized_prompt,
            "resolution": resolution,
            "generation_mode": generation_mode
        }
        
        if reference_bg_url:
            edit_inputs["image_context"] = reference_bg_url

        bg_gen_id = await trigger_generic_fashn_job(
            model_name="edit",  
            inputs=edit_inputs
        )
        
        final_local_urls = []
        while True:
            await asyncio.sleep(4) 
            status, output = await check_vton_status(bg_gen_id)
            if status == "completed":
                final_remote_urls = output if isinstance(output, list) else [output]
                for remote in final_remote_urls:
                    f_name = await download_and_save_remote_image(remote)
                    final_local_urls.append(f"{base_url}/static_uploads/{f_name}")
                break
            elif status == "failed":
                raise Exception("FASHN 'edit' step failed.")

        job.result_urls = final_local_urls
        job.status = models.JobStatus.COMPLETED
        db.commit()

    except Exception as e:
        logger.error(f"Background Change Chain Error on Job {job_id}: {str(e)}", exc_info=True)
        if 'job' in locals() and job:
            job.status = models.JobStatus.FAILED
            db.commit()
            
        sub = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user_id, 
            models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
        ).first()
        if sub:
            SubscriptionTransactionManager.refund_resources(db, sub, cost, "change_background", reference_id=job_id, reason=str(e))
    finally:
        db.close()


# ==========================================
# 4. CHANGE BACKGROUND
# ==========================================
@router.post("/change-background", response_model=StandardResponse)
async def change_background(
    background_tasks: BackgroundTasks,
    original_image: Optional[UploadFile] = File(None),
    generated_model_job_id: Optional[int] = Form(None),
    new_background_prompt: str = Form(...),
    reference_bg_image: Optional[UploadFile] = File(None),
    resolution: str = Form("2k"),
    generation_mode: str = Form("quality"),
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="change_background"))
):
    await ensure_fashn_credits_available(min_required=1.0)
    cost = SubscriptionTransactionManager.calculate_cost("change_background", subscription.plan_snapshot)

    try:
        orig_url = resolve_model_image_url(db, subscription.user_id, generated_model_job_id, original_image)
        ref_bg_url = None
        if reference_bg_image:
            ref_bg_url = await process_upload(reference_bg_image)
    except Exception as e:
        raise APIException(status_code=200, msg=f"Failed to process image assets: {str(e)}")

    try:
        prompt_lower = new_background_prompt.lower()
        is_night_scene = any(word in prompt_lower for word in ["night", "dark", "evening", "midnight", "dusk"])

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
            user_id=subscription.user_id, job_type=models.StudioJobType.BACKGROUND_CHANGE, input_data=input_data
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)

        SubscriptionTransactionManager.deduct_resources(db, subscription, cost, "change_background", reference_id=db_job.id)

    except Exception as e:
        db.rollback()
        raise APIException(status_code=200, msg=f"Database error while saving job: {str(e)}")

    try:
        background_tasks.add_task(
            process_advanced_background_chain,
            job_id=db_job.id,
            user_id=subscription.user_id,
            cost=cost,
            original_url=orig_url,
            harmonized_prompt=harmonized_prompt,
            reference_bg_url=ref_bg_url,
            resolution=resolution,
            generation_mode=generation_mode
        )

        return StandardResponse(status=True, msg="Advanced background replacement queued.", data={"job_id": db_job.id, "credits_deducted": cost})

    except Exception as e:
        raise APIException(status_code=200, msg=f"Failed to queue task: {str(e)}")


# ==========================================
# 5. MODEL CREATE
# ==========================================
def build_smart_gender_anchor(age_str: str, gender_str: str, ethnicity_str: str) -> Tuple[str, str]:
    try:
        age_int = int(age_str)
    except (ValueError, TypeError):
        age_int = 25
        
    g_lower = (gender_str or "female").strip().lower()
    e_str = (ethnicity_str or "Caucasian").strip()
    
    if g_lower == "male":
        noun = "male boy" if age_int < 18 else "young male man" if age_int <= 25 else "male man"
        gender_weight = "(masculine male features, handsome male face:1.4)"
    elif g_lower == "female":
        noun = "female girl" if age_int < 18 else "young female woman" if age_int <= 25 else "female woman"
        gender_weight = "(feminine female features, beautiful female face:1.4)"
    else:
        noun = f"{gender_str} person"
        gender_weight = ""
        
    anchor = f"a {age_int}-year-old {e_str} {noun}"
    return anchor, gender_weight


def sanitize_hair_details(hair_length: str, hair_color: str, hair_type: str, hair_style: str) -> str:
    length = (hair_length or "").strip()
    color = (hair_color or "").strip()
    htype = (hair_type or "").strip()
    style = (hair_style or "").strip()
    
    parts = []
    if length and length.upper() != "N/A": parts.append(length)
    if color and color.upper() != "N/A": parts.append(color)
    if htype and htype.upper() != "N/A": parts.append(f"{htype} texture")
    
    base_hair = ", ".join(parts) if parts else "neatly groomed hair"
    if style and style.upper() != "N/A":
        return f"{base_hair}, styled as {style}"
    return base_hair


def sanitize_facial_hair(beard_type: str, gender: str) -> str:
    beard_clean = (beard_type or "").strip()
    gender_lower = (gender or "").strip().lower()
    
    if gender_lower == "female":
        return ""
        
    if not beard_clean or beard_clean.upper() in ["N/A", "NONE", "CLEAN SHAVEN", "CLEAN-SHAVEN"]:
        return "clean-shaven face"
        
    if any(keyword in beard_clean.lower() for keyword in ["beard", "stubble", "mustache", "goatee"]):
        return beard_clean
        
    return f"{beard_clean} beard"


def sanitize_outfit_for_gender(outfit: str, gender: str) -> str:
    outfit_clean = (outfit or "").strip()
    if not outfit_clean:
        return "wearing a casual top"
    return f"wearing {outfit_clean}" if not outfit_clean.lower().startswith(("wearing", "in ", "clad")) else outfit_clean


def sanitize_background(setting: str) -> str:
    setting_clean = (setting or "").strip()
    if not setting_clean:
        return "standing in a minimalist photography studio setting"
    return setting_clean if setting_clean.lower().startswith(("standing", "sitting", "in ", "at ")) else f"standing in a {setting_clean}"


@router.post("/model-create", response_model=StandardResponse)
async def model_create(
    model_name: str = Form(...),
    age: Optional[str] = Form("25"),
    gender: Optional[str] = Form("Female"),
    ethnicity: Optional[str] = Form("Caucasian"),
    build_type: Optional[str] = Form("Slim"),
    hair_length: Optional[str] = Form("N/A"),
    hair_color: Optional[str] = Form("Dark brown"),
    hair_type: Optional[str] = Form("Wavy"),
    hair_style: Optional[str] = Form("Long flowing"),
    beard_type: Optional[str] = Form(""),
    eye_color: Optional[str] = Form("Deep brown"),
    face_shape: Optional[str] = Form("Oval"),
    jawline: Optional[str] = Form("Soft"),
    eyebrow: Optional[str] = Form("Arched"),
    face_expression: Optional[str] = Form("Calm"),
    skin_color: Optional[str] = Form("Fair"),
    camera_framing: Optional[str] = Form("Medium shot, from waist up"), 
    camera_angle: Optional[str] = Form("Eye-level angle"), 
    outfit_description: Optional[str] = Form("wearing a black ribbed tank top"),
    background_setting: Optional[str] = Form("standing in front of a modern gray tiled wall"),
    custom_prompt: Optional[str] = Form(None),                               
    image_reference: Optional[UploadFile] = File(None),                      
    face_reference: Optional[UploadFile] = File(None),                       
    face_reference_mode: str = Form("match_reference"),                      
    aspect_ratio: Optional[str] = Form(None),                                
    resolution: str = Form("1k"),                                            
    generation_mode: Optional[str] = Form(None),                             
    num_images: int = Form(1),                                               
    output_format: str = Form("png"),                                        
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="create_model_enabled", resource_key=models.ResourceKey.MODEL_CREATION))
):
    await ensure_fashn_credits_available(min_required=1.0)
    cost = SubscriptionTransactionManager.calculate_cost("model_create", subscription.plan_snapshot)
    
    if custom_prompt and custom_prompt.strip():
        final_prompt = custom_prompt.strip()
    else:
        subject_anchor, gender_weight = build_smart_gender_anchor(age, gender, ethnicity)
        clean_hair = sanitize_hair_details(hair_length, hair_color, hair_type, hair_style)
        clean_beard = sanitize_facial_hair(beard_type, gender)
        clean_outfit = sanitize_outfit_for_gender(outfit_description, gender)
        clean_background = sanitize_background(background_setting)
        
        beard_prompt = f"Facial hair details: {clean_beard}. " if clean_beard else ""
        
        final_prompt = (
            f"(Close-up {camera_framing}:1.4) from a {camera_angle}, "
            f"of {subject_anchor} {gender_weight} with a {build_type} body build. "
            f"The model has {skin_color} skin, an {face_shape} face shape, a {jawline} jawline, "
            f"and {eyebrow} eyebrows. Their eyes are {eye_color}, showing a {face_expression} expression. "
            f"Hair details: {clean_hair}. "
            f"{beard_prompt}"
            f"The model is {clean_outfit}. "
            f"The setting is {clean_background}. "
            f"Fashion photography, (strict waist-up portrait:1.5), (lower body is strictly out of frame:1.5), "
            f"{resolution}, photorealistic, cinematic lighting, shot on 85mm lens, high-definition."
        )

    fashn_input_data = {
        "prompt": final_prompt,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }
        
    db_input_data = {
        "prompt": final_prompt,
        "attributes": {
            "model_name": model_name,
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
                "skin_color": skin_color,
                "beard_type": beard_type
            }
        },
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }

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

    if aspect_ratio:
        fashn_input_data["aspect_ratio"] = aspect_ratio
        db_input_data["aspect_ratio"] = aspect_ratio
    if generation_mode:
        fashn_input_data["generation_mode"] = generation_mode
        db_input_data["generation_mode"] = generation_mode
    
    db_job = models.StudioJob(
        user_id=subscription.user_id, job_type=models.StudioJobType.MODEL_CREATE, input_data=db_input_data, is_active=True
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    SubscriptionTransactionManager.deduct_resources(
        db, subscription, cost, "model_create", quota_key=models.ResourceKey.MODEL_CREATION, reference_id=db_job.id
    )

    try:
        fashn_id = await trigger_generic_fashn_job(
            model_name="model-create",
            inputs=fashn_input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        return StandardResponse(status=True, msg="Model Create job started.", data={"job_id": db_job.id, "credits_deducted": cost})
    except Exception as e:
        db.rollback()
        SubscriptionTransactionManager.refund_resources(
            db, subscription, cost, "model_create", quota_key=models.ResourceKey.MODEL_CREATION, reference_id=db_job.id, reason=str(e)
        )
        raise APIException(status_code=200, msg=f"AI Engine failed: {str(e)}")


@router.get("/my-models", response_model=StandardResponse)
async def get_user_models(
    gender: Optional[str] = Query(None, description="Filter models by gender (e.g., 'Male' or 'Female')"), 
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Max number of records to return"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        base_query = db.query(models.StudioJob).filter(
            models.StudioJob.user_id == current_user.id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED,
            models.StudioJob.is_active == True
        )

        if gender:
            base_query = base_query.filter(
                func.lower(func.json_unquote(func.json_extract(models.StudioJob.input_data, '$.attributes.gender'))) == gender.lower()
            )

        total_count = base_query.count()
        jobs = base_query.order_by(models.StudioJob.id.desc()).offset(skip).limit(limit).all()

        formatted_jobs = []
        for job in jobs:
            input_dict = job.input_data if isinstance(job.input_data, dict) else (json.loads(job.input_data) if job.input_data else {})
            prompt_text = input_dict.get("prompt", "")
            attributes = input_dict.get("attributes", {}) 
            
            generated_images = []
            if job.result_urls:
                generated_images = job.result_urls if isinstance(job.result_urls, list) else json.loads(job.result_urls)
                
            job_status = job.status.value if hasattr(job.status, 'value') else job.status

            formatted_jobs.append({
                "job_id": job.id,
                "fashn_job_id": job.fashn_job_id,
                "status": job_status,
                "prompt": prompt_text,
                "attributes": attributes,  
                "generated_image_urls": generated_images, 
                "created_at": job.created_at,
                "updated_at": job.updated_at
            })

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
        raise APIException(status_code=200, msg=f"Failed to fetch models: {str(e)}")


@router.get("/model-detail/{job_id}", response_model=StandardResponse)
async def get_model_detail(
    job_id: int = Path(..., description="The unique ID of the model job"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        job = db.query(models.StudioJob).filter(
            models.StudioJob.id == job_id,
            models.StudioJob.user_id == current_user.id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED,
            models.StudioJob.is_active == True
        ).first()

        if not job:
            raise APIException(status_code=200, msg="Model not found or is no longer active.")

        input_dict = job.input_data if isinstance(job.input_data, dict) else (json.loads(job.input_data) if job.input_data else {})
        prompt_text = input_dict.get("prompt", "")
        attributes = input_dict.get("attributes", {}) 
        
        generated_images = []
        if job.result_urls:
            generated_images = job.result_urls[0] if isinstance(job.result_urls, list) else json.loads(job.result_urls)
            
        job_status = job.status.value if hasattr(job.status, 'value') else job.status

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

        return StandardResponse(
            status=True,
            msg="Model detail retrieved successfully.",
            data=formatted_job
        )
        
    except APIException:
        raise
    except Exception as e:
        raise APIException(status_code=200, msg=f"Failed to fetch model details: {str(e)}")


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
            raise APIException(status_code=200, msg="Job not found.")

        if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
            fashn_status, output_data = await check_vton_status(db_job.fashn_job_id)
            if fashn_status == "completed":
                urls_to_download = output_data if isinstance(output_data, list) else [output_data]
                local_urls = []
                for remote_url in urls_to_download:
                    filename = await download_and_save_remote_image(remote_url)
                    local_urls.append(f"{base_url}/static_uploads/{filename}")

                db_job.status = models.JobStatus.COMPLETED
                db_job.result_urls = local_urls
                db.commit()
            elif fashn_status == "failed":
                db_job.status = models.JobStatus.FAILED
                db.commit()

        return StandardResponse(
            status=True, msg="Job status retrieved.", 
            data={
                "id": db_job.id, "job_type": db_job.job_type.value if hasattr(db_job.job_type, 'value') else db_job.job_type,
                "status": db_job.status.value if hasattr(db_job.status, 'value') else db_job.status,
                "result_urls": db_job.result_urls
            }
        )

    except APIException:
        raise
    except Exception as e:
        logger.error(f"Error fetching studio status for Job {job_id}: {str(e)}", exc_info=True)
        raise APIException(status_code=200, msg="Internal error fetching status.")


# ==========================================
# 6. FACE TO MODEL
# ==========================================
@router.post("/face-to-model", response_model=StandardResponse)
async def face_to_model(
    face_image: Optional[UploadFile] = File(None),
    generated_model_job_id: Optional[int] = Form(None),
    prompt: Optional[str] = Form(None),
    aspect_ratio: str = Form("2:3"),
    resolution: str = Form("1k"),
    generation_mode: Optional[str] = Form(None),
    num_images: int = Form(1),
    output_format: str = Form("png"),
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="face_to_model"))
):
    await ensure_fashn_credits_available(min_required=1.0)
    cost = SubscriptionTransactionManager.calculate_cost("face_to_model", subscription.plan_snapshot)
    face_url = resolve_model_image_url(db, subscription.user_id, generated_model_job_id, face_image)

    input_data = {
        "face_image": face_url,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "num_images": num_images,
        "output_format": output_format
    }

    if prompt:
        input_data["prompt"] = prompt
    if generation_mode:
        input_data["generation_mode"] = generation_mode

    db_job = models.StudioJob(
        user_id=subscription.user_id, job_type=models.StudioJobType.FACE_TO_MODEL, input_data=input_data
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    SubscriptionTransactionManager.deduct_resources(db, subscription, cost, "face_to_model", reference_id=db_job.id)

    try:
        fashn_id = await trigger_generic_fashn_job(
            model_name="face-to-model",
            inputs=input_data
        )
        db_job.fashn_job_id = fashn_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()

        return StandardResponse(status=True, msg="Face-to-Model creation started.", data={"job_id": db_job.id, "credits_deducted": cost})
    except Exception as e:
        db.rollback()
        SubscriptionTransactionManager.refund_resources(db, subscription, cost, "face_to_model", reference_id=db_job.id, reason=str(e))
        raise APIException(status_code=200, msg=f"AI Engine failed: {str(e)}")