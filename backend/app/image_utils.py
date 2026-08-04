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
    current_user: models.User = Depends(get_current_user)
):
    """
    Standalone API utility to intelligently re-adjust incoming image dimensions 
    via real-time content mapping without degrading the underlying image ratio.
    """
    logger.info(f"User {current_user.id} initiated smart crop command with target ratio: {target_ratio}")
    
    if not image.content_type.startswith("image/"):
        raise APIException(status_code=400, msg="Provided payload asset must be a valid image type.")

    try:
        # 1. Stream input file down into localized scratch disks
        original_filename = save_upload_file(image)
        original_path = os.path.join(UPLOAD_DIR, original_filename)
        
        # 2. Structure target destination filenames for the processed result
        file_ext = original_filename.split(".")[-1] if "." in original_filename else "jpg"
        cropped_filename = f"crop_{uuid.uuid4().hex}.{file_ext}"
        cropped_path = os.path.join(UPLOAD_DIR, cropped_filename)
        
        # 3. Hand processing over to the Computer Vision Engine
        process_smart_crop(original_path, cropped_path, target_ratio)
        
        # 4. Construct production endpoints URLs dynamically
        base_url = settings.BACKEND_URL.rstrip("/")
        original_url = f"{base_url}/static_uploads/{original_filename}"
        cropped_url = f"{base_url}/static_uploads/{cropped_filename}"
        
        return StandardResponse(
            status=True,
            msg="Image intelligently adjusted and reframed successfully.",
            data={
                "original_url": original_url,
                "cropped_url": cropped_url,
                "aspect_ratio": target_ratio
            }
        )

    except ValueError as ve:
        logger.error(f"Value tracking mismatch during crop logic execution: {str(ve)}")
        raise APIException(status_code=400, msg=str(ve))
    except Exception as e:
        logger.error(f"Critical execution fault during smart crop calculation pipeline: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal processor error handling structural geometry updates.")