import os

from fastapi import FastAPI, Depends, File, UploadFile, Form,Request,APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import traceback
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy.orm import Session
from typing import Optional

from . import models
from .database import engine, get_db
from .exceptions import APIException
from .utils import save_upload_file

from .closet import router as closet_router
from .outfit import router as outfit_router
from .auth import get_current_user
from .schemas import StandardResponse
from .history import router as history_router
from .studio import router as studio_router
from .three_sixty import router as three_sixty_router
# from .image_utils import router as image_utils_router

from .fashn_service import trigger_vton_job, check_vton_status
from .models import MasterModuleType

from .auth import router as auth_router
from fastapi.staticfiles import StaticFiles

import logging
from logging.handlers import TimedRotatingFileHandler




# ==========================================
# Configure Production Logging
# ==========================================
# 1. Define the logs directory at the root level of your backend
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 2. Prevent duplicate logs if FastAPI reloads
if logger.hasHandlers():
    logger.handlers.clear()

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# 3. File Handler: Rotates daily at midnight, keeps 30 days of history
log_filename = os.path.join(LOGS_DIR, "vton_app.log")
file_handler = TimedRotatingFileHandler(
    filename=log_filename,
    when="midnight",
    interval=1,
    backupCount=30, 
    encoding="utf-8"
)
file_handler.suffix = "%Y-%m-%d.log" # Appends date like: vton_app.log.2026-06-27
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 4. Console Handler: Still prints to terminal/systemd for real-time monitoring
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


try:
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database schemas initialized successfully.")
except Exception as e:
    logger.critical(f"Failed to initialize database schemas: {str(e)}", exc_info=True)
    
router = APIRouter()
app = FastAPI(title="Virtual Try-On API Studio")


# Register Routers (ENSURE NO TRAILING SLASHES)
app.include_router(auth_router,  tags=["Auth"])
app.include_router(closet_router,  tags=["Closet"])
app.include_router(history_router)
app.include_router(outfit_router)
app.include_router(three_sixty_router)
app.include_router(studio_router)
# MOUNT SMART CROPPING ROUTER HERE:
# app.include_router(image_utils_router)
app.mount("/static_uploads", StaticFiles(directory="static_uploads"), name="static_uploads")


origins = [
    "https://vton.falcondetectives.com",
    "http://vton.falcondetectives.com",
    "http://localhost:5500", # Keep this for local testing
    "http://127.0.0.1:5500"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # Use the specific list here
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# Static Files Mount
# ==========================================
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static_uploads", StaticFiles(directory=UPLOAD_DIR), name="static_uploads")
logger.info(f"Mounted static uploads directory at: {UPLOAD_DIR}")


# ****************************  DEFINING THE Custom Exception *************************


        
 # ---------------------------------------------------------
# 1. CONTROLLED ERROR HANDLER (Respects your custom status codes)
# ---------------------------------------------------------
@app.exception_handler(APIException)
async def custom_api_exception_handler(request: Request, exc: APIException):
    """
    Catches deliberate exceptions like APIException(status_code=400, msg="Bad format")
    and returns the exact code you specified, rather than defaulting to 500.
    """
    # Log as a warning so you know it happened, but it isn't a server crash
    logger.warning(f"Controlled API Error [{exc.status_code}]: {exc.msg}")
    
    return JSONResponse(
        status_code=exc.status_code, # Injects the exact status code from the route
        content={
            "status": False,
            "msg": exc.msg,
            "data": exc.data
        }
    )

# ---------------------------------------------------------
# 2. GLOBAL CRASH HANDLER (Only catches true 500 failures)
# ---------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches ALL unexpected framework crashes (e.g., missing DB columns, syntax errors).
    Since these are true failures, they correctly default to 500.
    """
    error_trace = traceback.format_exc()
    
    # Log as a critical error with the full traceback
    logger.error(f"CRITICAL UNHANDLED ERROR:\n{error_trace}")
    
    return JSONResponse(
        status_code=500,
        content={
            "status": False, 
            "msg": str(exc), 
            "data": None
        }
    )
        
        
# ---------------------------------------------------------
# 3. PYDANTIC VALIDATION ERROR HANDLER
# ---------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Catches FastAPI/Pydantic validation errors (e.g., missing fields, bad email format)
    and forces them into our StandardResponse JSON format.
    """
    errors = exc.errors()
    
    # Extract the first error message to create a clean, readable 'msg'
    error_msg = "Data Validation Failed"
    if errors:
        first_error = errors[0]
        # Join the location array (e.g., 'body', 'email') to show which field failed
        field = " -> ".join(str(loc) for loc in first_error.get("loc", []))
        msg = first_error.get("msg", "")
        error_msg = f"Validation Error on '{field}': {msg}"
        
    logger.warning(f"Pydantic Validation Error: {error_msg}")
    
    return JSONResponse(
        status_code=422,
        content={
            "status": False,
            "msg": error_msg,
            "data": errors # Keep the raw error array in data for frontend debugging
        }
    )

# *************************************************************************************


@app.get("/",tags=["VTON Try-On API"])
def read_root():
    return {"message": "VTON Core Engine is running"}


@app.post("/api/tryon", response_model=StandardResponse,tags=["VTON Try-On API"])
async def create_tryon_job(
    category: models.GarmentCategory = Form(...),
    garment_desc: Optional[str] = Form(""), 
    person_image: UploadFile = File(...),
    garment_image: Optional[UploadFile] = File(None),
    closet_item_id: Optional[int] = Form(None),
    resolution: str = Form("1k"),
    output_format: str = Form("png"),
    num_images: int = Form(1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    logger.info(f"--- NEW TRY-ON REQUEST --- User ID: {current_user.id} | Category: {category.value}")
    base_url = "https://vton-backend.falcondetectives.com"
    
    # 1. Validation Logging
    logger.debug(f"Validating person_image: {person_image.filename} ({person_image.content_type})")
    if not person_image.content_type.startswith("image/"):
        logger.warning(f"Validation failed: Invalid person_image format by User {current_user.id}")
        raise APIException(status_code=400, msg="Invalid person_image format. Must be an image.")
    
    if garment_image:
        logger.debug(f"Validating garment_image: {garment_image.filename} ({garment_image.content_type})")
        if not garment_image.content_type.startswith("image/"):
            logger.warning(f"Validation failed: Invalid garment_image format by User {current_user.id}")
            raise APIException(status_code=400, msg="Invalid garment_image format. Must be an image.")
    
    try:
        # 2. Garment Source Resolution
        if closet_item_id:
            logger.info(f"Resolving closet_item_id: {closet_item_id} for User {current_user.id}")
            item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_item_id,
                models.ClosetItem.user_id == current_user.id
            ).first()
            if not item:
                logger.warning(f"Closet item {closet_item_id} not found or unauthorized for User {current_user.id}")
                raise APIException(status_code=404, msg="Closet item not found")
            
            path_part = item.file_path.replace("\\", "/") 
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"
            logger.debug(f"Closet item resolved to URL: {garment_url}")
            
        elif garment_image:
            logger.info("Saving uploaded raw garment image...")
            garment_filename = save_upload_file(garment_image)
            garment_url = f"{base_url}/static_uploads/{garment_filename}"
            logger.debug(f"Raw garment saved to URL: {garment_url}")
        else:
            raise APIException(status_code=400, msg="Must provide either garment_image or closet_item_id")

        # 3. Process Person Image
        logger.info("Saving uploaded person image...")
        person_filename = save_upload_file(person_image)
        person_url = f"{base_url}/static_uploads/{person_filename}"
        logger.debug(f"Person image saved to URL: {person_url}")

        # 4. Database Insertion
        logger.info("Creating local tracking database record...")
        db_job = models.TryOnJob(
            user_id=current_user.id,
            category=category,
            user_image_url=person_url,
            garment_image_url=garment_url,
            status=models.JobStatus.PENDING
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        logger.info(f"Local Job created successfully. Internal Job ID: {db_job.id}")

        # 5. Trigger FASHN API
        logger.info(f"Dispatching Job ID {db_job.id} to FASHN API Engine (Resolution: {resolution}, Samples: {num_images})...")
        fashn_job_id = await trigger_vton_job(
            model_image_url=person_url, 
            garment_image_url=garment_url, 
            category=category.value, 
            garment_desc=garment_desc,
            resolution=resolution,
            output_format=output_format,
            num_images=num_images
        )
        
        logger.info(f"FASHN API Engine accepted Job {db_job.id}. Assigned FASHN ID: {fashn_job_id}")
        db_job.fashn_job_id = fashn_job_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        logger.info(f"--- TRY-ON REQUEST COMPLETED SUCCESSFULLY FOR JOB {db_job.id} ---")

        return StandardResponse(
            status=True,
            msg="Try-on job created successfully.",
            data={
                "id": db_job.id,
                "user_id": db_job.user_id,
                "category": db_job.category.value, 
                "status": db_job.status.value,     
                "fashn_job_id": db_job.fashn_job_id,
                "user_image_url": db_job.user_image_url,
                "garment_image_url": db_job.garment_image_url
            }
        )
        
    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"CRITICAL FAILURE during try-on job creation for User {current_user.id}: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to initiate AI core. Please try again.")


@app.get("/api/tryon/{job_id}", response_model=StandardResponse,tags=["VTON Try-On API"])
async def get_tryon_status(
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user) 
):
    logger.info(f"--- STATUS POLL --- User {current_user.id} checking Job ID: {job_id}")
    try:
        # 1. Fetch Local Record
        db_job = db.query(models.TryOnJob).filter(
            models.TryOnJob.id == job_id,
            models.TryOnJob.user_id == current_user.id
        ).first()
        
        if not db_job:
            logger.warning(f"Status poll failed: Job {job_id} not found for User {current_user.id}")
            raise APIException(status_code=404, msg="Try-on job not found or access denied.")

        logger.debug(f"Job {job_id} found. Current local status: {db_job.status.value}")

        # 2. Return immediately if not processing
        if db_job.status in [models.JobStatus.COMPLETED, models.JobStatus.FAILED]:
            logger.info(f"Job {job_id} is already {db_job.status.value}. Returning cached record.")
            return StandardResponse(
                status=True,
                msg="Job status retrieved.",
                data={
                    "id": db_job.id,
                    "status": db_job.status.value,
                    "result_image_urls": db_job.result_image_urls
                }
            )

        # 3. Poll FASHN API if still processing
        if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
            logger.info(f"Job {job_id} is PROCESSING. Pinging FASHN API (FASHN ID: {db_job.fashn_job_id}) for live status...")
            fashn_status, result_urls = await check_vton_status(db_job.fashn_job_id)
            
            logger.info(f"FASHN API responded with status: '{fashn_status}' for Job {job_id}")

            # 4. Map and Save State
            if fashn_status == "completed":
                db_job.status = models.JobStatus.COMPLETED
                db_job.result_image_urls = result_urls
                db.commit()
                logger.info(f"SUCCESS: Job {job_id} finished generating! Result URL array saved to database.")
                
            elif fashn_status == "failed":
                db_job.status = models.JobStatus.FAILED
                db.commit()
                logger.error(f"FAILURE: FASHN AI Engine reported a failure for Job {job_id}.")

        return StandardResponse(
            status=True,
            msg="Job status retrieved.",
            data={
                "id": db_job.id,
                "status": db_job.status.value,
                "result_image_urls": db_job.result_image_urls
            }
        )
    except APIException:
        raise
    except Exception as e:
        logger.error(f"CRITICAL ERROR polling FASHN status for Job {job_id}: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal server error while polling job status.")
    
    
    
    
    
@app.get("/api/universal-status/{module_type}/{job_id}", response_model=StandardResponse,tags=["VTON Try-On API"])
async def universal_status_check(
    module_type: MasterModuleType,
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Master Status API routing specifically for Try-On, 360, and Outfit features.
    """
    logger.info(f"Universal Poll: User {current_user.id} checking {module_type.value} Job {job_id}")

    try:
        # ==========================================
        # ROUTE 1 & 2: TRY-ON & 360 GENERATION
        # Both utilize the TryOnJob table
        # ==========================================
        if module_type in [MasterModuleType.TRYON, MasterModuleType.THREE_SIXTY]:
            db_job = db.query(models.TryOnJob).filter(
                models.TryOnJob.id == job_id, 
                models.TryOnJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg=f"{module_type.value.title()} job not found.")
            
            if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
                fashn_status, output = await check_vton_status(db_job.fashn_job_id)
                
                if fashn_status == "completed":
                    db_job.status = models.JobStatus.COMPLETED
                    # Save to the JSON column safely
                    db_job.result_image_urls = output if isinstance(output, list) else [output]
                    db.commit()
                elif fashn_status == "failed":
                    db_job.status = models.JobStatus.FAILED
                    db.commit()

            return StandardResponse(
                status=True, 
                msg="Status retrieved", 
                data={
                    "id": db_job.id, 
                    "module": module_type.value, 
                    "status": db_job.status.value, 
                    "result_image_urls": db_job.result_image_urls
                }
            )

        # ==========================================
        # ROUTE 3: OUTFIT BUILDER (Background Chained)
        # ==========================================
        elif module_type == MasterModuleType.OUTFIT:
            db_job = db.query(models.OutfitJob).filter(
                models.OutfitJob.id == job_id, 
                models.OutfitJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="Outfit job not found.")
            
            # Outfit jobs poll internally via the background worker, 
            # so we ONLY safely return the local database state here.
            return StandardResponse(
                status=True, 
                msg="Status retrieved", 
                data={
                    "id": db_job.id, 
                    "module": module_type.value, 
                    "status": db_job.status.value, 
                    "result_image_url": db_job.result_image_url
                }
            )

    except APIException:
        raise
    except Exception as e:
        logger.error(f"Universal Polling Error: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal tracking error.")