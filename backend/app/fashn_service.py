import httpx
import asyncio
import json
import logging
from app.config import settings


# ==========================================
# 1. Inherit the master logger from main.py
# ==========================================

logger = logging.getLogger(__name__)

# ==========================================
# FASHN Endpoint Schemas
# ==========================================

FASHN_API_URL = "https://api.fashn.ai/v1/run"
FASHN_STATUS_URL = "https://api.fashn.ai/v1/status"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.FASHN_API_KEY}"
}

async def trigger_vton_job(
    model_image_url: str, 
    garment_image_url: str, 
    category: str, 
    garment_desc: str = "",
    resolution: str = "1k",
    output_format: str = "png",
    num_images: int = 1
) -> str:
    """
    Asynchronously fires the structured payload containing assets and prompts to the FASHN.ai tryon-max model.
    """
    logger.info(f"[FASHN SERVICE] Triggering try-on job for category '{category}'")
    
    
    payload = {
        "model_name": "tryon-max",
        "inputs": {
            "model_image": model_image_url,
            "product_image": garment_image_url,
            "resolution": resolution,
            "output_format": output_format,
            "num_images": num_images 
        }
    }
    
    # Prompt Augmentation Mapping
    category_instruction = ""
    if category == "tops":
        category_instruction = "worn on the upper body as a top"
    elif category == "bottoms":
        category_instruction = "worn on the lower body as pants/skirt"
    elif category == "one-pieces":
        category_instruction = "worn as a full body one-piece dress or suit"
    
    final_prompt = category_instruction
    if garment_desc and garment_desc.strip():
        final_prompt = f"{category_instruction}, {garment_desc.strip()}"
        
    if final_prompt:
        payload["inputs"]["prompt"] = final_prompt

    logger.info(f"[FASHN SERVICE] Payload structure successfully built. Outbound content check.")
    logger.debug(f"[FASHN SERVICE] Payload details: {json.dumps(payload)}")

    max_retries = 3
    for attempt in range(max_retries):
        logger.info(f"[FASHN SERVICE] Dispatching POST request to {FASHN_API_URL} (Attempt {attempt + 1}/{max_retries})")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(FASHN_API_URL, json=payload, headers=headers, timeout=30.0)
                response.raise_for_status() 
                
                data = response.json()
                fashn_id = data.get("id")
                logger.info(f"[FASHN SERVICE] Successfully triggered job. Received FASHN ID: {fashn_id}")
                return fashn_id
                
        except httpx.RequestError as exc:
            logger.warning(f"[FASHN SERVICE] Network timeout or connection error on attempt {attempt + 1}: {exc}")
        except httpx.HTTPStatusError as exc:
            logger.error(f"[FASHN SERVICE] API rejected request on attempt {attempt + 1}. HTTP {exc.response.status_code}: {exc.response.text}")
            
        if attempt < max_retries - 1:
            logger.info(f"[FASHN SERVICE] Sleeping for 2 seconds before retrying...")
            await asyncio.sleep(2)
            
    logger.error("[FASHN SERVICE] CRITICAL: All 3 attempts to reach FASHN API failed.")
    raise Exception("Failed to connect to FASHN API after multiple attempts.")
    

async def check_vton_status(fashn_job_id: str):
    logger.info(f"[FASHN SERVICE] Polling status for FASHN ID: {fashn_job_id}")
    try:
        async with httpx.AsyncClient() as client:
            target_url = f"{FASHN_STATUS_URL}/{fashn_job_id}"
            response = await client.get(target_url, headers=headers, timeout=10.0)
            response.raise_for_status()
            
            data = response.json()
            status = data.get("status") 
            output = data.get("output")
            
            logger.info(f"[FASHN SERVICE] Job {fashn_job_id} reported status: '{status}'")
            return status, output 
            
    except httpx.HTTPStatusError as exc:
        logger.error(f"[FASHN SERVICE] API returned HTTP {exc.response.status_code} while checking status for {fashn_job_id}: {exc.response.text}")
        raise Exception(f"Failed to fetch status: HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        logger.error(f"[FASHN SERVICE] Network error while checking status for {fashn_job_id}: {str(exc)}")
        raise Exception(f"Failed to fetch status due to network error: {str(exc)}")
    

async def trigger_generic_fashn_job(model_name: str, inputs: dict) -> str:
    """
    Universal service to trigger any FASHN.ai model (Video, Face Swap, Bg Remove, etc.)
    """
    logger.info(f"[FASHN SERVICE] Universal engine initializing task for model: {model_name}")
    payload = {
        "model_name": model_name,
        "inputs": inputs
    }

    max_retries = 3
    for attempt in range(max_retries):
        logger.info(f"[FASHN SERVICE] Dispatching generic POST request (Attempt {attempt + 1}/{max_retries})")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(FASHN_API_URL, json=payload, headers=headers, timeout=45.0)
                response.raise_for_status() 
                fashn_id = response.json().get("id")
                logger.info(f"[FASHN SERVICE] Generic engine task accepted. FASHN ID: {fashn_id}")
                return fashn_id
                
        except httpx.RequestError as exc:
            logger.warning(f"[FASHN SERVICE] Network error on attempt {attempt + 1}: {exc}")
        except httpx.HTTPStatusError as exc:
            logger.error(f"[FASHN SERVICE] FASHN API returned an error HTTP {exc.response.status_code}: {exc.response.text}")
            
        if attempt < max_retries - 1:
            await asyncio.sleep(2)
            
    logger.error(f"[FASHN SERVICE] CRITICAL: Generic model activation failed for {model_name}.")
    raise Exception(f"Failed to connect to FASHN API for model {model_name}.")