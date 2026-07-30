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
from .dashboard import router as dashboard_router
from .profile import router as profile_router
from .dynamic_config import router as config_router
from .image_utils import router as image_utils_router
from .support import router as support_routers

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
app.include_router(dashboard_router)
app.include_router(profile_router)
app.include_router(config_router)
app.include_router(support_routers)
# MOUNT SMART CROPPING ROUTER HERE:
app.include_router(image_utils_router)
app.mount("/static_uploads", StaticFiles(directory="static_uploads"), name="static_uploads")


origins = [
    "https://vton.falcondetectives.com",
    "http://vton.falcondetectives.com",
    "http://localhost:5500", # Keep this for local testing
    "http://127.0.0.1:5500",
    "http://localhost:5173",
    "http://192.168.1.8:5173",
    "https://vton.microcrm.in"
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
        # status_code=exc.status_code, # Injects the exact status code from the route
        status_code=200,
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
        status_code=200,
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


# @app.post("/api/tryon", response_model=StandardResponse,tags=["VTON Try-On API"])
# async def create_tryon_job(
#     category: models.GarmentCategory = Form(...),
#     closet_item_id: Optional[int] = Form(None),             # Option A: Pull from DB (Strict Validation)
#     garment_image: Optional[UploadFile] = File(None),       # Option B: Raw Upload (Bypasses Validation)
#     system_model_id: Optional[int] = Form(None),            # Option A: Pre-loaded System Model
#     person_image: Optional[UploadFile] = File(None),        # Option B: Manual Custom Canvas Upload
#     garment_desc: Optional[str] = Form(""), 
#     resolution: str = Form("1k"),
#     output_format: str = Form("png"),
#     num_images: int = Form(1),
#     db: Session = Depends(get_db),
#     current_user: models.User = Depends(get_current_user)
# ):
#     logger.info(f"--- NEW TRY-ON REQUEST --- User ID: {current_user.id} | Category: {category.value}")
#     base_url = "https://vton-backend.falcondetectives.com"
    
#     # 1. Validation Logging
#     logger.debug(f"Validating person_image: {person_image.filename} ({person_image.content_type})")
#     if not person_image.content_type.startswith("image/"):
#         logger.warning(f"Validation failed: Invalid person_image format by User {current_user.id}")
#         raise APIException(status_code=400, msg="Invalid person_image format. Must be an image.")
    
#     if garment_image:
#         logger.debug(f"Validating garment_image: {garment_image.filename} ({garment_image.content_type})")
#         if not garment_image.content_type.startswith("image/"):
#             logger.warning(f"Validation failed: Invalid garment_image format by User {current_user.id}")
#             raise APIException(status_code=400, msg="Invalid garment_image format. Must be an image.")
    
#     try:
#         # 2. Garment Source Resolution
#         if closet_item_id:
#             logger.info(f"Resolving closet_item_id: {closet_item_id} for User {current_user.id}")
#             item = db.query(models.ClosetItem).filter(
#                 models.ClosetItem.id == closet_item_id,
#                 models.ClosetItem.user_id == current_user.id
#             ).first()
#             if not item:
#                 logger.warning(f"Closet item {closet_item_id} not found or unauthorized for User {current_user.id}")
#                 raise APIException(status_code=404, msg="Closet item not found")
            
#             path_part = item.file_path.replace("\\", "/") 
#             if not path_part.startswith("/"):
#                 path_part = "/" + path_part
#             garment_url = f"{base_url}{path_part}"
#             logger.debug(f"Closet item resolved to URL: {garment_url}")
            
#         elif garment_image:
#             logger.info("Saving uploaded raw garment image...")
#             garment_filename = save_upload_file(garment_image)
#             garment_url = f"{base_url}/static_uploads/{garment_filename}"
#             logger.debug(f"Raw garment saved to URL: {garment_url}")
#         else:
#             raise APIException(status_code=400, msg="Must provide either garment_image or closet_item_id")

#         # 3. Process Person Image
#         logger.info("Saving uploaded person image...")
#         person_filename = save_upload_file(person_image)
#         person_url = f"{base_url}/static_uploads/{person_filename}"
#         logger.debug(f"Person image saved to URL: {person_url}")

#         # 4. Database Insertion
#         logger.info("Creating local tracking database record...")
#         db_job = models.TryOnJob(
#             user_id=current_user.id,
#             category=category,
#             user_image_url=person_url,
#             garment_image_url=garment_url,
#             status=models.JobStatus.PENDING
#         )
#         db.add(db_job)
#         db.commit()
#         db.refresh(db_job)
#         logger.info(f"Local Job created successfully. Internal Job ID: {db_job.id}")

#         # 5. Trigger FASHN API
#         logger.info(f"Dispatching Job ID {db_job.id} to FASHN API Engine (Resolution: {resolution}, Samples: {num_images})...")
#         fashn_job_id = await trigger_vton_job(
#             model_image_url=person_url, 
#             garment_image_url=garment_url, 
#             category=category.value, 
#             garment_desc=garment_desc,
#             resolution=resolution,
#             output_format=output_format,
#             num_images=num_images
#         )
        
#         logger.info(f"FASHN API Engine accepted Job {db_job.id}. Assigned FASHN ID: {fashn_job_id}")
#         db_job.fashn_job_id = fashn_job_id
#         db_job.status = models.JobStatus.PROCESSING
#         db.commit()
#         logger.info(f"--- TRY-ON REQUEST COMPLETED SUCCESSFULLY FOR JOB {db_job.id} ---")

#         return StandardResponse(
#             status=True,
#             msg="Try-on job created successfully.",
#             data={
#                 "id": db_job.id,
#                 "user_id": db_job.user_id,
#                 "category": db_job.category.value, 
#                 "status": db_job.status.value,     
#                 "fashn_job_id": db_job.fashn_job_id,
#                 "user_image_url": db_job.user_image_url,
#                 "garment_image_url": db_job.garment_image_url
#             }
#         )
        
#     except APIException:
#         raise
#     except Exception as e:
#         db.rollback()
#         logger.error(f"CRITICAL FAILURE during try-on job creation for User {current_user.id}: {str(e)}", exc_info=True)
#         raise APIException(status_code=500, msg="Failed to initiate AI core. Please try again.")


@app.post("/api/tryon", response_model=StandardResponse, tags=["VTON Try-On API"])
async def create_tryon_job(
    category: models.GarmentCategory = Form(...),
    
    # --- GARMENT SOURCES ---
    closet_item_id: Optional[int] = Form(None),             
    garment_image: Optional[UploadFile] = File(None), 
    
    # --- PERSON/CANVAS SOURCES ---
    system_model_id: Optional[int] = Form(None),            
    model_persona_id: Optional[int] = Form(None),           
    generated_model_job_id: Optional[int] = Form(None),     
    person_image: Optional[UploadFile] = File(None),
           
    # --- CONFIGURATIONS ---
    garment_desc: Optional[str] = Form(""), 
    resolution: str = Form("1k"),
    output_format: str = Form("png"),
    num_images: int = Form(1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    
    logger.info(f"--- NEW TRY-ON REQUEST --- User ID: {current_user.id} | Category: {category.value}")
    base_url = "https://vton-backend.falcondetectives.com"
    
    try:
       # ==========================================
        # 1. Resolve Garment Source
        # ==========================================
        
        if closet_item_id:
            logger.info(f"Resolving closet_item_id: {closet_item_id} for User {current_user.id}")
            closet_item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_item_id,
                models.ClosetItem.user_id == current_user.id
            ).first()
            
            if not closet_item:
                raise APIException(status_code=404, msg="Selected closet garment not found.")
                
            path_part = closet_item.file_path.replace("\\", "/") 
            if not path_part.startswith("/"):
                path_part = "/" + path_part
            garment_url = f"{base_url}{path_part}"
            
        elif garment_image:
            logger.debug(f"Validating custom garment_image: {garment_image.filename}")
            if not garment_image.content_type.startswith("image/"):
                raise APIException(status_code=400, msg="Invalid garment_image format. Must be an image.")
            
            logger.info("Saving uploaded custom garment image...")
            garment_filename = save_upload_file(garment_image)
            garment_url = f"{base_url}/static_uploads/{garment_filename}"
            
        else:
            raise APIException(status_code=400, msg="Workspace requires either a closet_item_id or a garment_image upload.")

        
        # ==========================================
        # 2. Resolve Person Canvas Source
        # ==========================================
        
        if system_model_id:
            logger.info(f"Resolving system_model_id: {system_model_id}")
            sys_model = db.query(models.SystemModel).filter(
                models.SystemModel.id == system_model_id,
                models.SystemModel.is_active == True
            ).first()
            
            if not sys_model:
                raise APIException(status_code=404, msg="Selected system model variant not found.")
                
            person_url = sys_model.base_image_url
            
            
        elif model_persona_id:
            logger.info(f"Resolving Admin Model Persona ID: {model_persona_id}")
            persona = db.query(models.ModelPersona).filter(
                models.ModelPersona.id == model_persona_id,
                models.ModelPersona.is_active == True
            ).first()
            
            if not persona:
                raise APIException(status_code=404, msg="Selected Model Persona not found or inactive.")
            
            # Format the URL properly if it's a relative path
            preview = persona.preview_image_url.replace("\\", "/")
            if not preview.startswith("http"):
                preview = f"{base_url}/{preview.lstrip('/')}"
            person_url = preview
            
        elif person_image:
            logger.debug(f"Validating custom person_image: {person_image.filename}")
            if not person_image.content_type.startswith("image/"):
                raise APIException(status_code=400, msg="Invalid person_image format. Must be an image.")
            
            logger.info("Saving uploaded custom person image...")
            person_filename = save_upload_file(person_image)
            person_url = f"{base_url}/static_uploads/{person_filename}"
            
            
        elif generated_model_job_id:
            logger.info(f"Resolving User's Generated AI Model from StudioJob ID: {generated_model_job_id}")
            studio_job = db.query(models.StudioJob).filter(
                models.StudioJob.id == generated_model_job_id,
                models.StudioJob.user_id == current_user.id,
                models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
                models.StudioJob.status == models.JobStatus.COMPLETED
            ).first()
            
            if not studio_job or not studio_job.result_urls:
                raise APIException(status_code=404, msg="Generated model not found or job has not completed yet.")
                
            urls = studio_job.result_urls if isinstance(studio_job.result_urls, list) else json.loads(studio_job.result_urls)
            if not urls:
                raise APIException(status_code=404, msg="No images found in the generated model.")
            
            person_url = urls[0]  # Extracts the first image from the generation output
            
        else:
            raise APIException(status_code=400, msg="Workspace requires either a system_model_id or a person_image asset.")

        # 3. Database Insertion
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

        # 4. Trigger FASHN API
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
        
        db_job.fashn_job_id = fashn_job_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()

        return StandardResponse(
            status=True,
            msg="Try-on inference execution initialized successfully.",
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
    
    
    
    
@app.get("/api/universal-status/{module_type}/{job_id}", response_model=StandardResponse, tags=["VTON Try-On API"])
async def universal_status_check(
    module_type: MasterModuleType,
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Master Status API routed specifically for Try-On, 360, and Outfit features.
    
    - TRYON: Queries TryOnJob table, polls FASHN directly if processing.
    - THREE_SIXTY: Queries TryOnJob table, background worker manages polling.
    - OUTFIT: Queries OutfitJob table, background worker manages polling (returns result_image_url).
    """
    logger.info(f"Universal Poll: User {current_user.id} checking {module_type.value} Job {job_id}")

    try:
        # ==========================================
        # ROUTE 1: SINGLE TRY-ON (models.TryOnJob)
        # ==========================================
        if module_type == MasterModuleType.TRYON:
            db_job = db.query(models.TryOnJob).filter(
                models.TryOnJob.id == job_id, 
                models.TryOnJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="Try-On job not found.")
            
            # Poll FASHN directly if still processing
            if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
                fashn_status, output = await check_vton_status(db_job.fashn_job_id)
                
                if fashn_status == "completed":
                    db_job.status = models.JobStatus.COMPLETED
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
        # ROUTE 2: 360 GENERATION (models.TryOnJob)
        # ==========================================
        # elif module_type == MasterModuleType.THREE_SIXTY:
        #     db_job = db.query(models.TryOnJob).filter(
        #         models.TryOnJob.id == job_id, 
        #         models.TryOnJob.user_id == current_user.id
        #     ).first()
            
        #     if not db_job: 
        #         raise APIException(status_code=404, msg="360 view job not found.")

        #     # Background task updates local DB record; return state safely
        #     return StandardResponse(
        #         status=True, 
        #         msg="Status retrieved", 
        #         data={
        #             "id": db_job.id, 
        #             "module": module_type.value, 
        #             "status": db_job.status.value, 
        #             "result_image_urls": db_job.result_image_urls
        #         }
        #     )
        
        
        # ==========================================
        # ROUTE 2: 360 GENERATION (models.TryOnJob)
        # ==========================================
        elif module_type == MasterModuleType.THREE_SIXTY:
            db_job = db.query(models.TryOnJob).filter(
                models.TryOnJob.id == job_id, 
                models.TryOnJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="360 view job not found.")

            # Convert result_image_urls dictionary to a clean list of URLs
            formatted_urls = []
            if db_job.result_image_urls:
                raw_urls = db_job.result_image_urls
                
                if isinstance(raw_urls, str):
                    try:
                        raw_urls = json.loads(raw_urls)
                    except Exception:
                        raw_urls = [raw_urls]

                if isinstance(raw_urls, dict):
                    formatted_urls = list(raw_urls.values())
                elif isinstance(raw_urls, list):
                    formatted_urls = raw_urls

            # Background task updates local DB record; return state safely with list format
            return StandardResponse(
                status=True, 
                msg="Status retrieved", 
                data={
                    "id": db_job.id, 
                    "module": module_type.value, 
                    "status": db_job.status.value, 
                    "result_image_urls": formatted_urls
                }
            )

        # ==========================================
        # ROUTE 3: OUTFIT BUILDER (models.OutfitJob)
        # ==========================================
        elif module_type == MasterModuleType.OUTFIT:
            db_job = db.query(models.OutfitJob).filter(
                models.OutfitJob.id == job_id, 
                models.OutfitJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="Outfit job not found.")

            # Background task updates local DB record; return singular result_image_url
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
    
    
    
@app.get("/api/universal-configs", response_model=StandardResponse, tags=["System Configurations"])
async def get_universal_configurations():
    """
    Universal API to fetch configuration options for media generation parameters.
    Currently serves static data. Designed to be replaced with a DB query in the future.
    """
    try:
        # ==========================================
        # TODO: FUTURE DATABASE INTEGRATION
        # ==========================================
        # config_record = db.query(models.MediaConfigs).first()
        # if config_record:
        #     return StandardResponse(status=True, msg="Success", data=config_record.to_dict())
        
        # 1. Define Static Fallback Data
        static_data = {
            "image_resolutions": [
                {"label": "Standard (1K)", "value": "1k"},
                {"label": "High Definition (2K)", "value": "2k"},
                {"label": "Ultra HD (4K)", "value": "4k"}
            ],
            "video_qualities": [
                {"label": "SD (480p)", "value": "480"},
                {"label": "HD (720p)", "value": "720"},
                {"label": "FHD (1080p)", "value": "1080"}
            ],
            "aspect_ratios": [
                {"label": "Cinematic (21:9)", "value": "21:9"},
                {"label": "Square (1:1)", "value": "1:1"},
                {"label": "Classic Landscape (3:2)", "value": "3:2"},
                {"label": "Standard Landscape (4:3)", "value": "4:3"},
                {"label": "Large Landscape (5:4)", "value": "5:4"},
                {"label": "Social Portrait (4:5)", "value": "4:5"},
                {"label": "Standard Portrait (3:4)", "value": "3:4"},
                {"label": "Classic Portrait (2:3)", "value": "2:3"},
                {"label": "Widescreen (16:9)", "value": "16:9"},
                {"label": "Mobile/Reels (9:16)", "value": "9:16"}
            ],
            "video-durations":[
                {"label":"5s","value":5},
                {"label":"10s","value":10},
            ]
        }
        
        # 2. Return Payload
        return StandardResponse(
            status=True,
            msg="Universal media configurations retrieved successfully.",
            data=static_data
        )

    except Exception as e:
        logger.error(f"Error fetching universal configurations: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to load system configurations.")