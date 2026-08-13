import os
import uuid
import logging
from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .auth import get_current_user
from .schemas import StandardResponse
from .exceptions import APIException
from .utils import save_upload_file
from .image_processor import process_smart_crop
from .config import settings  # ADDED: Centralized config import

from .gatekeeper import PlanGatekeeper, SubscriptionTransactionManager # Import dependencies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/utils", tags=["Image Utilities"])

UPLOAD_DIR = "static_uploads"

# Ensure runtime save targets exist safely on startup
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/smart-crop", response_model=StandardResponse)
async def api_smart_crop(
    image: UploadFile = File(...),
    target_ratio: str = Form("9:16"),
    db: Session = Depends(get_db),
    subscription: models.UserSubscription = Depends(PlanGatekeeper()) # Secure Gatekeeper
):
    """
    Standalone API utility to intelligently re-adjust incoming image dimensions 
    via real-time content mapping without degrading the underlying image ratio.
    """
    logger.info(f"User {subscription.user_id} initiated smart crop command")
    
    if not image.content_type.startswith("image/"):
        raise APIException(status_code=400, msg="Provided payload asset must be a valid image type.")
    
    
    # cost = SubscriptionTransactionManager.calculate_cost("smart_crop", subscription.plan_snapshot)
    cost = SubscriptionTransactionManager.calculate_cost(
    db=db, 
    subscription_plan_id=subscription.subscription_plan_id, 
    action_key="smart_crop"
    )
    
    # 1. Stream input file down into localized scratch disks
    original_filename = save_upload_file(image)
    original_path = os.path.join(UPLOAD_DIR, original_filename)
    
    file_ext = original_filename.split(".")[-1] if "." in original_filename else "jpg"
    cropped_filename = f"crop_{uuid.uuid4().hex}.{file_ext}"
    cropped_path = os.path.join(UPLOAD_DIR, cropped_filename)
    
    # 2. Deduct upfront
    SubscriptionTransactionManager.deduct_resources(db, subscription, cost, "smart_crop")

    try:
        process_smart_crop(original_path, cropped_path, target_ratio)
        
        base_url = settings.BACKEND_URL.rstrip("/")
        
        return StandardResponse(
            status=True,
            msg="Image intelligently adjusted and reframed successfully.",
            data={
                "original_url": f"{base_url}/static_uploads/{original_filename}",
                "cropped_url": f"{base_url}/static_uploads/{cropped_filename}",
                "aspect_ratio": target_ratio,
                "credits_deducted": cost
            }
        )

    except Exception as e:
        # Refund on engine failure
        SubscriptionTransactionManager.refund_resources(db, subscription, cost, "smart_crop", reason=str(e))
        logger.error(f"Critical execution fault: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal processor error.")