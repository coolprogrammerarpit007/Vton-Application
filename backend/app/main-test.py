import os
import logging
from logging.handlers import TimedRotatingFileHandler
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import Optional
from . import models, schemas
from .database import engine, get_db
from .utils import save_upload_file

from .fashn_service import trigger_vton_job, check_vton_status
from fastapi.middleware.cors import CORSMiddleware

from .auth import router as auth_router
from fastapi.staticfiles import StaticFiles
from .closet import router as closet_router



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
    

app = FastAPI(title="Virtual Try-On API Studio")
app.include_router(auth_router)
app.include_router(closet_router)
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


@app.get("/")
def read_root():
    return {"message": "VTON Core Engine is running"}



@app.post("/api/tryon", response_model=schemas.TryOnJobOut)
async def create_tryon_job(
    user_id: int = Form(...),
    category: models.GarmentCategory = Form(...),
    garment_desc: Optional[str] = Form(""), 
    person_image: UploadFile = File(...),
    garment_image: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    logger.info(f"Incoming try-on request | User ID: {user_id} | Category: {category.value}")

    # 1. Verify user profile exists
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        logger.warning(f"Request rejected: User ID {user_id} not found in database.")
        raise HTTPException(status_code=404, detail="User profile target not found")

    # 2. Save binary files down to disk storage safely
    try:
        person_filename = save_upload_file(person_image)
        garment_filename = save_upload_file(garment_image)
        logger.info(f"Files saved successfully: {person_filename}, {garment_filename}")
    except Exception as e:
        logger.error(f"Disk Write Error: Failed to save uploaded images.", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while saving files to disk.")
    
    # ==========================================
    # PRODUCTION ROUTING APPLIED
    # ==========================================
    base_url = "http://127.0.0.1:8000//static_uploads" 
    person_url = f"{base_url}/{person_filename}"
    garment_url = f"{base_url}/{garment_filename}"

    # 3. Create entry in local tracking database
    try:
        db_job = models.TryOnJob(
            user_id=user_id,
            category=category,
            user_image_url=person_url,
            garment_image_url=garment_url,
            status=models.JobStatus.PENDING
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        logger.info(f"Database record created | Job ID: {db_job.id}")
    except Exception as e:
        db.rollback() # Undo the transaction to prevent database corruption
        logger.error("Database Transaction Error: Could not create TryOnJob record.", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while saving job state.")

    # 4. Trigger external generation payload asynchronously
    try:
        logger.info(f"Dispatching Job ID {db_job.id} to FASHN.ai servers...")
        fashn_job_id = await trigger_vton_job(
            model_image_url=person_url, 
            garment_image_url=garment_url, 
            category=category.value, 
            garment_desc=garment_desc
        )
        
        # Update the database with the external tracking ID
        db_job.fashn_job_id = fashn_job_id
        db_job.status = models.JobStatus.PROCESSING
        db.commit()
        logger.info(f"FASHN.ai successfully received request | FASHN Tracker ID: {fashn_job_id}")
        
    except Exception as e:
        # If FASHN fails (e.g., 502 Bad Gateway), we mark our local database job as FAILED
        db_job.status = models.JobStatus.FAILED
        db.commit()
        logger.error(f"FASHN API Integration Error on Job ID {db_job.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to initiate AI core: {str(e)}")

    return db_job


@app.get("/api/tryon/{job_id}", response_model=schemas.TryOnJobOut)
async def get_tryon_status(job_id: int, db: Session = Depends(get_db)):
    # 1. Fetch the local job record
    db_job = db.query(models.TryOnJob).filter(models.TryOnJob.id == job_id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Try-on job not found")

    # 2. If it is already finished or failed, just return it immediately
    if db_job.status in [models.JobStatus.COMPLETED, models.JobStatus.FAILED]:
        return db_job

    # 3. If it is still processing, ask FASHN for a live update
    if db_job.status == models.JobStatus.PROCESSING and db_job.fashn_job_id:
        try:
            fashn_status, result_url = await check_vton_status(db_job.fashn_job_id)

            # 4. Map FASHN's server status to our local database
            if fashn_status == "completed":
                db_job.status = models.JobStatus.COMPLETED
                db_job.result_image_url = result_url
                db.commit()
                logger.info(f"Job {job_id} completed successfully! Result URL saved.")
                
            elif fashn_status == "failed":
                db_job.status = models.JobStatus.FAILED
                db.commit()
                logger.warning(f"Job {job_id} failed on FASHN servers.")
                
            # If FASHN says 'processing' or 'starting', we do nothing and let it remain PROCESSING

        except Exception as e:
            logger.error(f"Error polling FASHN status for Job {job_id}: {str(e)}", exc_info=True)

    return db_job