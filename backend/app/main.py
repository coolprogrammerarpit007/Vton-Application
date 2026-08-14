import os
import json
import traceback
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

from fastapi import FastAPI, Depends, File, UploadFile, Form, Request, APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from collections import defaultdict

from fastapi.concurrency import run_in_threadpool

from . import models
from .database import engine, get_db
from .exceptions import APIException
from .utils import save_upload_file,download_and_save_remote_image
from .config import settings  # ADDED: Centralized config import

# Gatekeeper & Transaction Ledger
from .gatekeeper import PlanGatekeeper, SubscriptionTransactionManager

# Routers
from .closet import router as closet_router
from .outfit import router as outfit_router
from .auth import get_current_user,router as auth_router
from .schemas import StandardResponse
from .history import router as history_router
from .studio import router as studio_router
from .three_sixty import router as three_sixty_router
from .dashboard import router as dashboard_router
from .profile import router as profile_router
from .dynamic_config import router as config_router
from .image_utils import router as image_utils_router
from .support import router as support_routers
from .plans import router as plans_router
from .payment import router as payment_router
from .faq import router as faq_router
from .location import router as location_router
from .pages import router as page_router


from .fashn_service import trigger_vton_job, check_vton_status
from .models import MasterModuleType,AspectRatio,UniversalConfig

# ==========================================
# Configure Production Logging
# ==========================================
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if logger.hasHandlers():
    logger.handlers.clear()

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

log_filename = os.path.join(LOGS_DIR, "vton_app.log")
file_handler = TimedRotatingFileHandler(
    filename=log_filename,
    when="midnight",
    interval=1,
    backupCount=30, 
    encoding="utf-8"
)
file_handler.suffix = "%Y-%m-%d.log" 
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


try:
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database schemas initialized successfully.")
except Exception as e:
    logger.critical(f"Failed to initialize database schemas: {str(e)}", exc_info=True)
    
app = FastAPI(title="Virtual Try-On API Studio")

# Register Routers
app.include_router(auth_router, tags=["Auth"])
app.include_router(closet_router, tags=["Closet"])
app.include_router(history_router)
app.include_router(outfit_router)
app.include_router(three_sixty_router)
app.include_router(studio_router)
app.include_router(dashboard_router)
app.include_router(profile_router)
app.include_router(config_router)
app.include_router(support_routers)
app.include_router(image_utils_router)
app.include_router(plans_router)
app.include_router(payment_router)
app.include_router(faq_router)
app.include_router(location_router)
app.include_router(page_router)

origins = [
    "https://vton.falcondetectives.com",
    "http://vton.falcondetectives.com",
    "http://localhost:5500", 
    "http://127.0.0.1:5500",
    "http://localhost:5173",
    "http://192.168.1.8:5173",
    "https://vton.microcrm.in"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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


# ==========================================
# Exception Handlers
# ==========================================
@app.exception_handler(APIException)
async def custom_api_exception_handler(request: Request, exc: APIException):
    logger.warning(f"Controlled API Error [{exc.status_code}]: {exc.msg}")
    return JSONResponse(
        status_code=200,
        content={
            "status": False,
            "msg": exc.msg,
            "data": exc.data
        }
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_trace = traceback.format_exc()
    logger.error(f"CRITICAL UNHANDLED ERROR:\n{error_trace}")
    return JSONResponse(
        status_code=500,
        content={
            "status": False, 
            "msg": str(exc), 
            "data": None
        }
    )
        
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_msg = "Data Validation Failed"
    if errors:
        first_error = errors[0]
        field = " -> ".join(str(loc) for loc in first_error.get("loc", []))
        msg = first_error.get("msg", "")
        error_msg = f"Validation Error on '{field}': {msg}"
        
    logger.warning(f"Pydantic Validation Error: {error_msg}")
    
    return JSONResponse(
        status_code=200,
        content={
            "status": False,
            "msg": error_msg,
            "data": errors 
        }
    )

# ==========================================
# API Routes
# ==========================================
@app.get("/", tags=["VTON Try-On API"])
def read_root():
    return {"message": "VTON Core Engine is running"}


# ==========================================
# VTON TRY-ON API (2 CREDITS DEDUCTION)
# ==========================================


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
    # Gatekeeper: Verifies virtual_try_on feature flag and active subscription
    subscription: models.UserSubscription = Depends(PlanGatekeeper(feature_flag="virtual_try_on"))
):
    
    logger.info(f"--- NEW TRY-ON REQUEST --- User ID: {subscription.user_id} | Category: {category.value}")
    base_url = settings.BACKEND_URL.rstrip("/")
    
     # Enforce 4K resolution check
    if resolution == "4k" and subscription.plan_snapshot.get("image_quality", "2k") == "2k":
        raise APIException(status_code=403, msg="4K render quality requires the Gold or Platinum plan.")

    # Calculate credit cost (2 credits)
    cost = SubscriptionTransactionManager.calculate_cost(
    db=db, 
    subscription_plan_id=subscription.subscription_plan_id, 
    action_key="virtual_try_on", 
    params={"num_images": num_images, "resolution": resolution}
)
    
    
    # ==========================================
    # 0. Conflict Validation
    # ==========================================
    if closet_item_id and garment_image:
        raise APIException(status_code=400, msg="Provide either a closet item or an image upload for the garment, not both.")
        
    person_sources = [system_model_id, model_persona_id, generated_model_job_id, person_image]
    if sum(bool(source) for source in person_sources) > 1:
        raise APIException(status_code=400, msg="Provide only one person canvas source (system model, persona, generated model, or image upload).")
    
    
    try:
        # ==========================================
        # 1. Resolve Garment Source
        # ==========================================
        if closet_item_id:
            logger.info(f"Resolving closet_item_id: {closet_item_id} for User {subscription.user_id}")
            closet_item = db.query(models.ClosetItem).filter(
                models.ClosetItem.id == closet_item_id,
                models.ClosetItem.user_id == subscription.user_id
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
            # Use threadpool to prevent blocking the async event loop during disk I/O
            garment_filename = await run_in_threadpool(save_upload_file, garment_image)
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
            
            preview = persona.preview_image_url.replace("\\", "/")
            if not preview.startswith("http"):
                preview = f"{base_url}/{preview.lstrip('/')}"
            person_url = preview
            
        elif person_image:
            logger.debug(f"Validating custom person_image: {person_image.filename}")
            if not person_image.content_type.startswith("image/"):
                raise APIException(status_code=400, msg="Invalid person_image format. Must be an image.")
            
            logger.info("Saving uploaded custom person image...")
            # Use threadpool to prevent blocking the async event loop
            person_filename = await run_in_threadpool(save_upload_file, person_image)
            person_url = f"{base_url}/static_uploads/{person_filename}"
            
            
        elif generated_model_job_id:
            logger.info(f"Resolving User's Generated AI Model from StudioJob ID: {generated_model_job_id}")
            studio_job = db.query(models.StudioJob).filter(
                models.StudioJob.id == generated_model_job_id,
                models.StudioJob.user_id == subscription.user_id,
                models.StudioJob.job_type == models.StudioJobType.MODEL_CREATE,
                models.StudioJob.status == models.JobStatus.COMPLETED
            ).first()
            
            if not studio_job or not studio_job.result_urls:
                raise APIException(status_code=404, msg="Generated model not found or job has not completed yet.")
                
            urls = []
            if isinstance(studio_job.result_urls, list):
                urls = studio_job.result_urls
            elif isinstance(studio_job.result_urls, str):
                try:
                    urls = json.loads(studio_job.result_urls)
                except json.JSONDecodeError:
                    raise APIException(status_code=500, msg="Corrupted image data in generated model record.")
                
            if not urls:
                raise APIException(status_code=404, msg="No images found in the generated model.")
            
            person_url = urls[0] 
            
        else:
            raise APIException(status_code=400, msg="Workspace requires either a system_model_id or a person_image asset.")

       # 3. Create Local Tracking Record
        db_job = models.TryOnJob(
            user_id=subscription.user_id,
            feature_name = "Virtual Tryon",
            category=category,
            user_image_url=person_url,
            garment_image_url=garment_url,
            prompt = garment_desc,
            status=models.JobStatus.PENDING
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)

        # 4. Deduct Credits Atomically
        SubscriptionTransactionManager.deduct_resources(
            db, subscription, cost, "tryon", reference_id=db_job.id
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"CRITICAL FAILURE preparing try-on job for User {subscription.user_id}: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to prepare job assets. Please try again.")
    
    
    
    #  ==========================================
    # 4. Trigger External API with Safe Fallback
    # ==========================================
    try:
        logger.info(f"Dispatching Job ID {db_job.id} to FASHN API Engine (Resolution: {resolution}, Samples: {num_images})...")
        fashn_job_id = await trigger_vton_job(
            db=db,
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
                "garment_image_url": db_job.garment_image_url,
                "credits_deducted": cost
            }
        )
        
    except APIException as custom_err:
        # Preserve specific error message and refund credits
        db_job.status = models.JobStatus.FAILED
        db.commit()
        SubscriptionTransactionManager.refund_resources(
            db, subscription, cost, "tryon", reference_id=db_job.id, reason=custom_err.msg
        )
        raise custom_err

    except Exception as api_error:
        logger.error(f"FASHN API Trigger failed for Job {db_job.id}: {str(api_error)}", exc_info=True)
        db_job.status = models.JobStatus.FAILED
        db.commit()

        SubscriptionTransactionManager.refund_resources(
            db, subscription, cost, "tryon", reference_id=db_job.id, reason=str(api_error)
        )
        raise APIException(status_code=200, msg="Failed to initiate AI core. Your credits have been refunded.")

@app.get("/api/tryon/{job_id}", response_model=StandardResponse,tags=["VTON Try-On API"])
async def get_tryon_status(
    job_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user) 
):
    logger.info(f"--- STATUS POLL --- User {current_user.id} checking Job ID: {job_id}")
    try:
        db_job = db.query(models.TryOnJob).filter(
            models.TryOnJob.id == job_id,
            models.TryOnJob.user_id == current_user.id
        ).first()
        
        if not db_job:
            logger.warning(f"Status poll failed: Job {job_id} not found for User {current_user.id}")
            raise APIException(status_code=404, msg="Try-on job not found or access denied.")

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

        if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
            logger.info(f"Job {job_id} is PROCESSING. Pinging FASHN API (FASHN ID: {db_job.fashn_job_id}) for live status...")
            fashn_status, result_urls = await check_vton_status(db_job.fashn_job_id)
            
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
    logger.info(f"Universal Poll: User {current_user.id} checking {module_type.value} Job {job_id}")
    
    # ADD THIS LINE TO DEFINE BASE_URL:
    base_url = settings.BACKEND_URL.rstrip("/")

    try:
        # ==========================================
        # ROUTE 1: SINGLE TRY-ON
        # ==========================================
        if module_type == MasterModuleType.TRYON:
            db_job = db.query(models.TryOnJob).filter(
                models.TryOnJob.id == job_id, 
                models.TryOnJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="Try-On job not found.")
            
            if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
                fashn_status, output = await check_vton_status(db_job.fashn_job_id)
                
                if fashn_status == "completed":
                    urls_to_download = output if isinstance(output, list) else [output]
                    local_urls = []
                    
                    for remote_url in urls_to_download:
                        filename = await download_and_save_remote_image(remote_url)
                        # base_url is now safely defined for this append operation
                        local_urls.append(f"{base_url}/static_uploads/{filename}")

                    db_job.status = models.JobStatus.COMPLETED
                    db_job.result_image_urls = local_urls
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
        # ROUTE 2: 360 GENERATION
        # ==========================================
        elif module_type == MasterModuleType.THREE_SIXTY:
            db_job = db.query(models.TryOnJob).filter(
                models.TryOnJob.id == job_id, 
                models.TryOnJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="360 view job not found.")

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
        # ROUTE 3: OUTFIT BUILDER
        # ==========================================
        elif module_type == MasterModuleType.OUTFIT:
            db_job = db.query(models.OutfitJob).filter(
                models.OutfitJob.id == job_id, 
                models.OutfitJob.user_id == current_user.id
            ).first()
            
            if not db_job: 
                raise APIException(status_code=404, msg="Outfit job not found.")

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
    
# @app.get("/api/universal-configs", response_model=StandardResponse, tags=["System Configurations"])
# async def get_universal_configurations():
#     try:
#         static_data = {
#             "image_resolutions": [
#                 {"label": "Standard (1K)", "value": "1k"},
#                 {"label": "High Definition (2K)", "value": "2k"},
#                 {"label": "Ultra HD (4K)", "value": "4k"}
#             ],
#             "video_qualities": [
#                 {"label": "SD (480p)", "value": "480"},
#                 {"label": "HD (720p)", "value": "720"},
#                 {"label": "FHD (1080p)", "value": "1080"}
#             ],
#             "aspect_ratios": [
#                 {"label": "Cinematic (21:9)", "value": "21:9"},
#                 {"label": "Square (1:1)", "value": "1:1"},
#                 {"label": "Classic Landscape (3:2)", "value": "3:2"},
#                 {"label": "Standard Landscape (4:3)", "value": "4:3"},
#                 {"label": "Large Landscape (5:4)", "value": "5:4"},
#                 {"label": "Social Portrait (4:5)", "value": "4:5"},
#                 {"label": "Standard Portrait (3:4)", "value": "3:4"},
#                 {"label": "Classic Portrait (2:3)", "value": "2:3"},
#                 {"label": "Widescreen (16:9)", "value": "16:9"},
#                 {"label": "Mobile/Reels (9:16)", "value": "9:16"}
#             ],
#             "video-durations":[
#                 {"label":"5s","value":5},
#                 {"label":"10s","value":10},
#             ]
#         }
        
#         return StandardResponse(
#             status=True,
#             msg="Universal media configurations retrieved successfully.",
#             data=static_data
#         )

#     except Exception as e:
#         logger.error(f"Error fetching universal configurations: {str(e)}", exc_info=True)
#         raise APIException(status_code=500, msg="Failed to load system configurations.")


@app.get(
    "/api/universal-configs",
    response_model=StandardResponse,
    tags=["System Configurations"],
)
async def get_universal_configurations(db: Session = Depends(get_db)):
    try:
        # 1. Fetch active universal configs ordered by sort_order
        universal_configs = (
            db.query(UniversalConfig)
            .filter(UniversalConfig.is_active == True)
            .order_by(UniversalConfig.sort_order.asc())
            .all()
        )

        formatted_data = defaultdict(list)

        for item in universal_configs:
            # Convert numeric strings (like duration '5') back to integers
            val = int(item.value) if item.value.isdigit() else item.value
            formatted_data[item.config_type].append(
                {"label": item.label, "value": val}
            )

        # # 2. Fetch aspect ratios dynamically from the aspect_ratios table
        # aspect_ratios = db.query(AspectRatio).all()

        # formatted_data["aspect_ratios"] = [
        #     {"label": ar.ratio, "value": ar.ratio} for ar in aspect_ratios
        # ]

        return StandardResponse(
            status=True,
            msg="Universal media configurations retrieved successfully.",
            data=dict(formatted_data),
        )

    except Exception as e:
        logger.error(
            f"Error fetching universal configurations: {str(e)}", exc_info=True
        )
        raise APIException(
            status_code=500, msg="Failed to load system configurations."
        )