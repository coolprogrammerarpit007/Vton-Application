import asyncio
import logging
import json
import re
from typing import Optional, Dict, List

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

router = APIRouter(prefix="/api/outfit", tags=["Outfit Builder"])


# ==============================================================================
# 1. SMART PROMPT DISAMBIGUATOR & TOKEN SANITIZER
# ==============================================================================
def sanitize_and_extract_layer_prompt(layer_category: models.OutfitLayer, raw_user_prompt: str) -> str:
    """
    Parses user prompts and isolates ONLY tokens relevant to the target layer,
    preventing cross-layer prompt leakage.
    """
    if not raw_user_prompt or not raw_user_prompt.strip():
        return ""

    prompt_lower = raw_user_prompt.lower()
    
    bottom_keywords = ["jean", "jeans", "pant", "pants", "trouser", "trousers", "palazzo", "skirt", "shorts", "legging", "salwar", "cargo"]
    top_keywords = ["top", "shirt", "t-shirt", "tshirt", "kurti", "kurta", "blouse", "crop", "sweater", "hoodie", "polo", "flannel"]
    outerwear_keywords = ["jacket", "coat", "blazer", "dupatta", "scarf", "shrug", "cardigan", "cape", "draped"]

    clauses = re.split(r'[,;.]|\band\b', prompt_lower)
    relevant_clauses = []

    if layer_category == models.OutfitLayer.BOTTOM:
        target_keys = bottom_keywords
        exclude_keys = top_keywords + outerwear_keywords
    elif layer_category == models.OutfitLayer.TOP:
        target_keys = top_keywords
        exclude_keys = bottom_keywords + outerwear_keywords
    elif layer_category == models.OutfitLayer.OUTERWEAR:
        target_keys = outerwear_keywords
        exclude_keys = bottom_keywords
    else:
        target_keys = []
        exclude_keys = []

    for clause in clauses:
        clause_str = clause.strip()
        if not clause_str:
            continue
        has_target = any(k in clause_str for k in target_keys)
        has_exclude = any(k in clause_str for k in exclude_keys)
        if has_target and not has_exclude:
            relevant_clauses.append(clause_str)

    if not relevant_clauses:
        clean_text = raw_user_prompt
        for bad_word in exclude_keys:
            clean_text = re.sub(rf'\b{bad_word}\b', '', clean_text, flags=re.IGNORECASE)
        return clean_text.strip()

    return ", ".join(relevant_clauses)


# def build_intelligent_garment_prompt(
#     layer_category: models.OutfitLayer, 
#     raw_user_prompt: str = "",
#     garment_item_category: str = ""
# ) -> str:
#     """
#     Dynamically constructs prompts enforcing 100% original garment preservation,
#     full-body framing, and preventing AI alterations unless explicitly requested.
#     """
#     clean_layer_prompt = sanitize_and_extract_layer_prompt(layer_category, raw_user_prompt)
#     prompt_search = (clean_layer_prompt + " " + raw_user_prompt + " " + garment_item_category).lower()

#     # Detect Explicit User Intent for Modifications
#     is_unbutton_requested = any(k in prompt_search for k in ["open", "unbuttoned", "unzipped", "open shirt", "open jacket"])
#     is_cape_requested = any(k in prompt_search for k in ["cape", "draped over shoulder", "over shoulders", "shoulder drape"])
#     is_ethnic_top = any(k in prompt_search for k in ["kurti", "kurta", "anarkali", "tunic", "ethnic top", "salwar suit"])
#     is_dupatta = any(k in prompt_search for k in ["dupatta", "scarf", "shawl"])

#     # Strict Preservation Directive
#     strict_preservation_anchor = (
#         "Preserve 100% exact original garment design, fabric print, neckline cut, collar, "
#         "buttons/closures, and overall garment structure as depicted in the garment image. "
#         "Do not alter necklines, do not open buttons, do not modify outfit structure."
#     )

#     framing_directive = (
#         "Full length portrait, head-to-toe full body shot, fully visible head, face, "
#         "legs, and shoes. Two natural human arms resting at sides, clean anatomy, no extra limbs."
#     )

#     # --- 1. BOTTOMS LAYER ---
#     if layer_category == models.OutfitLayer.BOTTOM:
#         return (
#             f"Preserve exact trouser/pant cut, waistline, pattern, length, and drape from original garment image. "
#             f"{clean_layer_prompt}. {framing_directive}"
#         ).strip()

#     # --- 2. TOPS LAYER ---
#     elif layer_category == models.OutfitLayer.TOP:
#         if is_unbutton_requested:
#             button_instruction = "Shirt worn open over inner undershirt."
#         else:
#             button_instruction = "Keep all buttons and closures neatly fastened/closed exactly as shown in original garment image."

#         if is_ethnic_top:
#             neck_instruction = (
#                 "Preserve original high/closed neckline, collar, and neck yoke pattern up to collarbones. "
#                 "Do not make off-shoulder, do not cut shoulders or alter neck cut. Long straight kurti hemline with side slits."
#             )
#         else:
#             neck_instruction = "Preserve exact neck cut, collar structure, and sleeve length from original garment image."

#         return f"{strict_preservation_anchor} {button_instruction} {neck_instruction} {clean_layer_prompt}. {framing_directive}".strip()

#     # --- 3. OUTERWEAR / DUPATTA LAYER ---
#     elif layer_category == models.OutfitLayer.OUTERWEAR:
#         if is_dupatta:
#             return f"Traditional dupatta scarf draped cleanly over shoulder as designed. {clean_layer_prompt}. {framing_directive}".strip()
#         elif is_cape_requested:
#             return (
#                 f"Jacket draped over shoulders like a cape without putting arms through sleeves. "
#                 f"Empty jacket sleeves hanging naturally at sides, model's arms remain inside inner shirt sleeves. "
#                 f"{clean_layer_prompt}. {framing_directive}"
#             ).strip()
#         else:
#             if is_unbutton_requested:
#                 outerwear_closure = "Outerwear jacket worn open."
#             else:
#                 outerwear_closure = "Preserve original jacket closures, zipper, and buttons as shown in original garment image."
                
#             return f"{strict_preservation_anchor} {outerwear_closure} {clean_layer_prompt}. {framing_directive}".strip()

#     return f"{strict_preservation_anchor} {clean_layer_prompt}. {framing_directive}".strip()



def build_intelligent_garment_prompt(
    layer_category: models.OutfitLayer, 
    raw_user_prompt: str = "",
    garment_item_category: str = ""
) -> str:
    clean_layer_prompt = sanitize_and_extract_layer_prompt(layer_category, raw_user_prompt)
    prompt_search = (clean_layer_prompt + " " + raw_user_prompt + " " + garment_item_category).lower()

    # Detect Explicit User Intent for Modifications
    is_unbutton_requested = any(k in prompt_search for k in ["open", "unbuttoned", "unzipped", "open shirt", "open jacket"])
    is_cape_requested = any(k in prompt_search for k in ["cape", "draped over shoulder", "over shoulders", "shoulder drape"])
    is_ethnic_indian = any(k in prompt_search for k in ["kurti", "kurta", "anarkali", "tunic", "ethnic top", "salwar suit"])
    is_dupatta = any(k in prompt_search for k in ["dupatta", "scarf", "shawl"])
    
    # NEW: Detect East Asian garments
    is_ethnic_asian = any(k in prompt_search for k in ["hanfu", "qipao", "kimono", "yukata", "cheongsam"])

    strict_preservation_anchor = (
        "Preserve 100% exact original garment design, fabric print, neckline cut, collar, "
        "buttons/closures, and overall garment structure as depicted in the garment image. "
        "Do not alter necklines, do not open buttons, do not modify outfit structure."
    )

    framing_directive = (
        "Full length portrait, head-to-toe full body shot, fully visible head, face, "
        "legs, and shoes. Two natural human arms resting at sides, clean anatomy, no extra limbs."
    )

    if layer_category == models.OutfitLayer.BOTTOM:
        return f"Preserve exact trouser/pant cut, waistline, pattern, length, and drape from original garment image. {clean_layer_prompt}. {framing_directive}".strip()

    elif layer_category == models.OutfitLayer.TOP:
        if is_unbutton_requested:
            button_instruction = "Shirt worn open over inner undershirt."
        else:
            button_instruction = "Keep all buttons and closures neatly fastened/closed exactly as shown in original garment image."

        # Handle specific necklines and drapes based on culture
        if is_ethnic_indian:
            neck_instruction = "Preserve original high/closed neckline, collar, and neck yoke pattern up to collarbones. Do not make off-shoulder, do not cut shoulders. Long straight kurti hemline with side slits."
        elif is_ethnic_asian:
            neck_instruction = "Preserve traditional overlapping lapels, Mandarin collars, and wide flowing traditional sleeves exactly as shown. Maintain authentic cultural drape."
        else:
            neck_instruction = "Preserve exact neck cut, collar structure, and sleeve length from original garment image."

        return f"{strict_preservation_anchor} {button_instruction} {neck_instruction} {clean_layer_prompt}. {framing_directive}".strip()

    elif layer_category == models.OutfitLayer.OUTERWEAR:
        if is_dupatta:
            return f"Traditional dupatta scarf draped cleanly over shoulder as designed. {clean_layer_prompt}. {framing_directive}".strip()
        elif is_cape_requested:
            return f"Jacket draped over shoulders like a cape without putting arms through sleeves. Empty jacket sleeves hanging naturally at sides. {clean_layer_prompt}. {framing_directive}".strip()
        else:
            outerwear_closure = "Outerwear jacket worn open." if is_unbutton_requested else "Preserve original jacket closures, zipper, and buttons as shown in original garment image."
            return f"{strict_preservation_anchor} {outerwear_closure} {clean_layer_prompt}. {framing_directive}".strip()

    return f"{strict_preservation_anchor} {clean_layer_prompt}. {framing_directive}".strip()



# ==============================================================================
# 2. OPTIMIZED ASYNCHRONOUS CHAINING ENGINE
# ==============================================================================
async def process_outfit_chain(
    job_id: int, 
    user_id: int,
    cost: int,
    job_type: str,
    resolution: str, 
    output_format: str
):
    """
    Background worker executing sequential virtual try-on layering with smart fast polling.
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

        # Execution order: Bottom -> Top -> Outerwear -> Accessory
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

        logger.info(f"Initiating AI Chain for Job {job_id} | Total Layers: {total_layers}")

        for index, garment in enumerate(sorted_garments):
            logger.info(f"Processing Layer [{index + 1}/{total_layers}]: {garment.layer_category.value}")
            
            path_part = garment.closet_item.file_path.replace("\\", "/")
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"
            
            closet_category_str = getattr(garment.closet_item, "category", "").lower()
            
            if garment.layer_category == models.OutfitLayer.BOTTOM:
                fashn_category = "bottoms"
            elif any(word in closet_category_str for word in ["dress", "one-piece", "gown", "jumpsuit"]):
                fashn_category = "one-pieces"
            else:
                fashn_category = "tops"

            dynamic_prompt = build_intelligent_garment_prompt(
                layer_category=garment.layer_category, 
                raw_user_prompt=job.styling_prompt or "",
                garment_item_category=closet_category_str
            )
            logger.info(f"Layer [{garment.layer_category.value}] Fashn Category: '{fashn_category}' | Prompt: '{dynamic_prompt}'")

            fashn_id = await trigger_vton_job(
                db=db,  
                model_image_url=current_base_image,
                garment_image_url=garment_url,
                category=fashn_category,
                garment_desc=dynamic_prompt,
                resolution=resolution,
                output_format=output_format,
                num_images=1  
            )
            
            # --- HIGH-SPEED POLLING OPTIMIZATION ---
            # Initial 6-second sleep (since Fashn jobs take minimum 6s)
            await asyncio.sleep(6)
            
            single_result_url = None
            while True:
                status, output_data = await check_vton_status(fashn_id)
                if status == "completed":
                    remote_url = output_data[0] if isinstance(output_data, list) else output_data
                    local_filename = await download_and_save_remote_image(remote_url)
                    single_result_url = f"{base_url}/static_uploads/{local_filename}"
                    break
                elif status == "failed":
                    err_details = output_data if isinstance(output_data, str) else "FASHN generation failed"
                    raise Exception(f"FASHN generation failed on layer '{garment.layer_category.value}': {err_details}")
                
                # Fast 1.5-second polling interval after initial wait
                await asyncio.sleep(1.5)
            
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
            
        sub = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user_id, 
            models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
        ).first()
        if sub:
            SubscriptionTransactionManager.refund_resources(db, sub, cost, job_type, reference_id=job_id, reason=str(e))
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
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="outerwear_enabled"))
):
    logger.info(f"Multi-garment composition requested by User ID: {subscription.user_id}")
    base_url = settings.BACKEND_URL.rstrip('/')
    
    # 1. Pre-flight check on upstream Fashn master wallet credits
    await ensure_fashn_credits_available(db=db,min_required=1.0)
    
    # 1. Always calculate as Outerwear (Flat 6 Credits for Gold/Platinum)
    # cost = SubscriptionTransactionManager.calculate_cost("outerwear", subscription.plan_snapshot)
    cost = SubscriptionTransactionManager.calculate_cost(
    db=db, 
    subscription_plan_id=subscription.subscription_plan_id, 
    action_key="outfit_generation", 
    params={"resolution": resolution}
)
    
    # 2. Validate at least one clothing ID was passed
    selected_garments = [id for id in [top_closet_id, bottom_closet_id, outerwear_closet_id] if id is not None]
    if not selected_garments:
        raise APIException(status_code=200, msg="Outfit creation requires at least one closet garment.")

    person_url: Optional[str] = None
    
    if generated_model_job_id:
        studio_job = db.query(models.StudioJob).filter(
            models.StudioJob.id == generated_model_job_id,
            models.StudioJob.user_id == subscription.user_id,
            models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
            models.StudioJob.status == models.JobStatus.COMPLETED
        ).first()
        
        
        if not studio_job or not studio_job.result_urls:
            raise APIException(status_code=200, msg="Selected generated model not found or job has not completed yet.")
            
        urls = studio_job.result_urls if isinstance(studio_job.result_urls, list) else json.loads(studio_job.result_urls)
        if not urls:
            raise APIException(status_code=200, msg="No images found in the selected generated model.")
            
        person_url = urls[0]
            
    elif person_image:
        if not person_image.content_type.startswith("image/"):
            raise APIException(status_code=200, msg="Invalid person_image format. Must be an image.")
            
        person_filename = save_upload_file(person_image)
        person_url = f"{base_url}/static_uploads/{person_filename}"
        
    if not person_url:
        raise APIException(
            status_code=200, 
            msg="Must provide either a person_image upload or a valid generated_model_job_id."
        )

    # 4. Database Job Creation
    db_job = models.OutfitJob(
        user_id=subscription.user_id, person_image_url=person_url,top_closet_id=top_closet_id,bottom_closet_id=bottom_closet_id,outwear_closet_id=outerwear_closet_id ,status=models.JobStatus.PENDING, styling_prompt=outfit_desc
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    # 5. Bind Relational Metadata
    def attach_layer_relationship(closet_id: Optional[int], layer_type: models.OutfitLayer):
        if not closet_id:
            return
            
        item = db.query(models.ClosetItem).filter(
            models.ClosetItem.id == closet_id,
            models.ClosetItem.user_id == subscription.user_id
        ).first()
        
        if not item:
            raise APIException(
                status_code=200, 
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
        raise APIException(status_code=200, msg="Failed to bind relational metadata mapping arrays.")
    
     # 6. Deduct 6 Credits Atomically
    SubscriptionTransactionManager.deduct_resources(db, subscription, cost, "outerwear", reference_id=db_job.id)
    
    # 7. Queue Background Chain
    background_tasks.add_task(
        process_outfit_chain, 
        job_id=db_job.id, 
        user_id=subscription.user_id,
        cost=cost,
        job_type="outerwear",
        resolution=resolution, 
        output_format=output_format
    )
    return StandardResponse(
        status=True,
        msg="Outfit composition successfully queued.",
        data={"outfit_job_id": db_job.id, "credits_deducted": cost}
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