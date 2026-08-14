import httpx
import asyncio
import json
import logging
from typing import Tuple, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.exceptions import APIException
from . import models

# ==========================================
# 1. Inherit the master logger from main.py
# ==========================================

logger = logging.getLogger(__name__)

FASHN_API_URL = "https://api.fashn.ai/v1/run"
FASHN_STATUS_URL = "https://api.fashn.ai/v1/status"
FASHN_ACCOUNT_URL = "https://api.fashn.ai/v1/credits"


COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.FASHN_API_KEY}"
}

HTTP_TIMEOUT = httpx.Timeout(45.0, connect=15.0)


# ==========================================
# 1. Master Wallet Telemetry & Pre-Check
# ==========================================
async def check_fashn_master_balance() -> float:
    """
    Queries Fashn.ai master account credit balance directly via HTTP.
    (Kept for Admin Syncing and Payment initialization checks, not used for per-job latency).
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
        try:
            response = await client.get(FASHN_ACCOUNT_URL, headers=COMMON_HEADERS)
            if response.status_code == 200:
                data = response.json()
                credits_data = data.get("credits", {})
                return float(credits_data.get("total", 0.0))
                
            logger.warning(f"[FASHN SERVICE] Master balance status: {response.status_code}")
            return -1.0
        except Exception as exc:
            logger.error(f"[FASHN SERVICE] Failed to fetch master balance: {str(exc)}")
            return -1.0
        
        
async def ensure_fashn_credits_available(db: Session, min_required: float = 0.10):
    """
    Pre-flight guard: Checks local Master Wallet to prevent latency.
    Prevents dispatching generation jobs if upstream master wallet is depleted.
    """
    cr_sum = db.query(func.sum(models.MpxFashnApiPayment.fashn_amount)).filter(
        models.MpxFashnApiPayment.amount_type == 'cr'
    ).scalar() or 0.0

    dr_sum = db.query(func.sum(models.MpxFashnApiPayment.fashn_amount)).filter(
        models.MpxFashnApiPayment.amount_type == 'dr'
    ).scalar() or 0.0

    # FIX: Cast to float individually BEFORE subtraction
    wholesale_balance = float(cr_sum) - float(dr_sum)

    if wholesale_balance < min_required:
        logger.critical(f"[FASHN SERVICE] CIRCUIT BREAKER: Local Master wallet depleted ({wholesale_balance} remaining).")
        raise APIException(
            status_code=200,
            msg="The AI generation engines are currently undergoing scheduled maintenance. Please try again shortly."
        )


# ==========================================
# 2. Virtual Try-On Engine (tryon-max)
# ==========================================

async def trigger_vton_job(
    db:Session,
    model_image_url: str, 
    garment_image_url: str, 
    category: str, 
    garment_desc: str = "", 
    resolution: str = "1k", 
    output_format: str = "png", 
    num_images: int = 1,
    generation_mode: str = "balanced"
) -> str:
    """
    Asynchronously fires the structured payload containing assets and prompts to the FASHN.ai tryon-max model.
    """
    
    # 1. Local Database Pre-flight check
    await ensure_fashn_credits_available(db, min_required=0.05)
    
    logger.info(f"[FASHN SERVICE] Triggering VTON job for category '{category}' (Mode: {generation_mode})")
    
    
    
    # Prompt Augmentation Mapping
    category_instruction = ""
    if category == "tops":
        category_instruction = "worn on the upper body as topwear"
    elif category == "bottoms":
        category_instruction = "worn on the lower body as pants/skirt"
    elif category == "one-pieces":
        category_instruction = "worn as a full body one-piece dress or suit"
        
    
    
    prompt_parts = [p.strip() for p in [category_instruction, garment_desc] if p and p.strip()]
    final_prompt = ", ".join(prompt_parts)
    
    payload = {
        "model_name": "tryon-max",
        "inputs": {
            "model_image": model_image_url,
            "product_image": garment_image_url,
            "resolution": resolution,
            "generation_mode": generation_mode,
            "output_format": output_format,
            "num_images": num_images
        }
    }

    if final_prompt:
        payload["inputs"]["prompt"] = final_prompt

    logger.info(f"[FASHN SERVICE] Payload structure successfully built. Outbound content check.")
    logger.debug(f"[FASHN SERVICE] Payload details: {json.dumps(payload)}")

    max_retries = 3
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for attempt in range(max_retries):
            logger.info(f"[FASHN SERVICE] Dispatching VTON Request (Attempt {attempt + 1}/{max_retries})")
            try:
                response = await client.post(FASHN_API_URL, json=payload, headers=COMMON_HEADERS)

                # --- API-LEVEL ERROR SPECIFICATION HANDLERS ---
                if response.status_code == 400:
                    logger.error(f"[FASHN SERVICE] BadRequest (HTTP 400): {response.text}")
                    raise APIException(status_code=200, msg="Invalid request parameters sent to AI Engine.")

                if response.status_code == 401:
                    logger.critical("[FASHN SERVICE] UnauthorizedAccess (HTTP 401): Invalid API Key.")
                    raise APIException(status_code=200, msg="AI Engine authorization failed.")

                if response.status_code == 404:
                    logger.error(f"[FASHN SERVICE] NotFound (HTTP 404): {response.text}")
                    raise APIException(status_code=200, msg="AI Engine endpoint or resource not found.")

                if response.status_code == 402:
                    logger.critical("[FASHN SERVICE] CIRCUIT BREAKER: Master Wallet empty (HTTP 402).")
                    raise APIException(status_code=200, msg="The AI generation engines are currently undergoing maintenance.")

                if response.status_code == 429:
                    error_data = response.json() if response.content else {}
                    error_code = error_data.get("error", {}).get("name", "") or error_data.get("name", "")
                    
                    if response.status_code == 429:
                        error_data = response.json() if response.content else {}
                        error_code = error_data.get("error", {}).get("name", "") or error_data.get("name", "")
                    
                    if "OutOfCredits" in error_code or "insufficient" in str(error_data).lower():
                        logger.critical("[FASHN SERVICE] OutOfCredits (HTTP 429): Upstream master wallet exhausted.")
                        raise APIException(status_code=200, msg="The AI generation engines are currently undergoing maintenance.")

                    logger.warning(f"[FASHN SERVICE] Rate/Concurrency limit hit ({error_code}). Retrying in 4s...")
                    await asyncio.sleep(4)
                    continue

                response.raise_for_status()
                data = response.json()
                fashn_id = data.get("id")

                if not fashn_id:
                    raise APIException(status_code=200, msg="AI Engine accepted request but failed to issue a job ID.")

                logger.info(f"[FASHN SERVICE] Job accepted. Received FASHN ID: {fashn_id}")
                return fashn_id

            except APIException:
                raise
            except httpx.HTTPStatusError as exc:
                logger.error(f"[FASHN SERVICE] Upstream HTTP Error {exc.response.status_code}: {exc.response.text}")
            except httpx.RequestError as exc:
                logger.warning(f"[FASHN SERVICE] Connection error on attempt {attempt + 1}: {exc}")

            if attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))

    raise APIException(status_code=200, msg="Failed to connect to AI Generation Engine after multiple attempts.")
    

# ==========================================
# 3. Resilient Status Poller (Runtime Error Handler)
# ==========================================
async def check_vton_status(fashn_job_id: str) -> Tuple[str, Optional[Any]]:
    """
    Polls processing status and parses runtime errors (ImageLoadError, ContentModerationError, etc.).
    """
    target_url = f"{FASHN_STATUS_URL}/{fashn_job_id}"
    poll_timeout = httpx.Timeout(30.0, connect=15.0)
    
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=poll_timeout) as client:
                response = await client.get(target_url, headers=COMMON_HEADERS)
                
                if response.status_code == 429:
                    logger.warning(f"[FASHN SERVICE] Status polling rate-limited (HTTP 429) for {fashn_job_id}.")
                    return "in_progress", None
                
                response.raise_for_status()
                data = response.json()
                status = data.get("status")
                output = data.get("output")
                
                # --- RUNTIME ERROR SPECIFICATION PARSER ---
                if status == "failed":
                    error_obj = data.get("error", {})
                    err_name = error_obj.get("name", "RuntimeError")
                    err_msg = error_obj.get("message", "Model failed during execution.")
                    
                    logger.error(f"[FASHN SERVICE] Runtime Failure on Job {fashn_job_id}: {err_name} - {err_msg}")
                    
                    if err_name == "ImageLoadError":
                        user_msg = "Failed to load input image asset. Please verify the uploaded image URL is valid."
                    elif err_name == "ContentModerationError":
                        user_msg = "The input image or prompt violated safety/content moderation policies."
                    elif err_name == "InputValidationError":
                        user_msg = "Inconsistent or invalid image parameters provided."
                    else:
                        user_msg = f"AI Execution failed: {err_msg}"
                        
                    return "failed", user_msg

                return status, output

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
            logger.warning(f"[FASHN SERVICE] Network glitch on status poll attempt {attempt + 1}: {exc}")
            if attempt == 0:
                await asyncio.sleep(2.0)
                continue
            return "in_progress", None

        except APIException:
            raise
        except Exception as exc:
            logger.error(f"[FASHN SERVICE] Unexpected status poll error for {fashn_job_id}: {str(exc)}")
            return "in_progress", None

    return "in_progress", None


# ==========================================
# 4. Universal Engine Dispatcher
# ==========================================
async def trigger_generic_fashn_job(db:Session,model_name: str, inputs: dict) -> str:
    """
    Universal service for auxiliary Fashn models (model-create, model-swap, etc.).
    """
    # 1. Local Database Pre-flight check
    await ensure_fashn_credits_available(db=db,min_required=0.10)

    logger.info(f"[FASHN SERVICE] Initializing generic task for model: '{model_name}'")
    payload = {
        "model_name": model_name,
        "inputs": inputs
    }

    max_retries = 3
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for attempt in range(max_retries):
            logger.info(f"[FASHN SERVICE] Dispatching generic POST request (Attempt {attempt + 1}/{max_retries})")
            try:
                response = await client.post(FASHN_API_URL, json=payload, headers=COMMON_HEADERS)

                if response.status_code == 400:
                    raise APIException(status_code=200, msg=f"Invalid payload for {model_name}.")
                if response.status_code == 401:
                    raise APIException(status_code=200, msg="AI Engine authentication failed.")
                if response.status_code == 402:
                    raise APIException(status_code=200, msg="The AI generation engines are undergoing maintenance.")

                if response.status_code == 429:
                    error_data = response.json() if response.content else {}
                    error_code = error_data.get("error", {}).get("name", "") or error_data.get("name", "")
                    
                    if "OutOfCredits" in error_code or "insufficient" in str(error_data).lower():
                        raise APIException(status_code=200, msg="The AI generation engines are undergoing maintenance.")

                    logger.warning(f"[FASHN SERVICE] Rate limit hit ({error_code}). Retrying in 4s...")
                    await asyncio.sleep(4)
                    continue

                response.raise_for_status()
                fashn_id = response.json().get("id")

                if not fashn_id:
                    raise APIException(status_code=200, msg=f"Model {model_name} did not return a job ID.")

                return fashn_id

            except APIException:
                raise
            except httpx.HTTPStatusError as exc:
                logger.error(f"[FASHN SERVICE] Generic Model HTTP Error {exc.response.status_code}: {exc.response.text}")
            except httpx.RequestError as exc:
                logger.warning(f"[FASHN SERVICE] Network error for {model_name} on attempt {attempt + 1}: {exc}")

            if attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))

    raise APIException(status_code=200, msg=f"Failed to connect to AI Engine for model {model_name}.")