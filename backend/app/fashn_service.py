import httpx
import os
from app.config import settings

FASHN_API_URL = "https://api.fashn.ai/v1/run"
FASHN_STATUS_URL = "https://api.fashn.ai/v1/status"

# Use the API key securely loaded from your .env file
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.FASHN_API_KEY}"
}

async def trigger_vton_job(model_image_url: str, garment_image_url: str, category: str, garment_desc: str = "") -> str:
    """
    Asynchronously fires the structured payload containing assets and prompts to the FASHN.ai tryon-max model.
    """
    # Build nested target layout schema per official playground specs
    payload = {
        "model_name": "tryon-max",
        "inputs": {
            "model_image": model_image_url,
            "product_image": garment_image_url,
        }
    }
    
    # 2. CATEGORY UTILIZATION: Prompt Augmentation
    # We translate your category (tops, bottoms, one-pieces) into explicit AI instructions
    category_instruction = ""
    if category == "tops":
        category_instruction = "worn on the upper body as a top"
    elif category == "bottoms":
        category_instruction = "worn on the lower body as pants/skirt"
    elif category == "one-pieces":
        category_instruction = "worn as a full body one-piece dress or suit"
    
    # 3. Combine the auto-instruction with the user's manual description (if any)
    final_prompt = category_instruction
    if garment_desc and garment_desc.strip():
        # If the user typed something like "tucked in", we append it
        final_prompt = f"{category_instruction}, {garment_desc.strip()}"
        
    # Inject the enhanced prompt into the payload
    if final_prompt:
        payload["inputs"]["prompt"] = final_prompt

    async with httpx.AsyncClient() as client:
        response = await client.post(FASHN_API_URL, json=payload, headers=headers, timeout=30.0)
        
        if response.status_code != 200:
            raise Exception(f"FASHN API Engine connection error: {response.text}")
            
        data = response.json()
        return data.get("id")
    
    
FASHN_STATUS_URL = "https://api.fashn.ai/v1/status"

async def check_vton_status(fashn_job_id: str):
    """
    Checks the current status of an ongoing FASHN.ai prediction.
    Returns a tuple: (status_string, result_image_url_or_none)
    """
    async with httpx.AsyncClient() as client:
        # 1. Make a GET request to the official status endpoint using the specific job ID
        response = await client.get(f"{FASHN_STATUS_URL}/{fashn_job_id}", headers=headers, timeout=10.0)
        
        if response.status_code != 200:
            raise Exception(f"FASHN Status Retrieval Error: {response.text}")
            
        data = response.json()
        
        # 2. Extract the exact status state 
        status = data.get("status") 
        
        # 3. Securely extract the output URL if the job is finished
        output = data.get("output")
        result_url = output[0] if isinstance(output, list) and len(output) > 0 else None
        
        return status, result_url