import os
import uuid
import shutil
from fastapi import UploadFile

# Define the directory where uploaded files will be stored permanently
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static_uploads")

# Ensure the upload directory exists on the Linux/Windows server filesystem
os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_upload_file(upload_file: UploadFile) -> str:
    """
    Safely saves an uploaded file to the disk using streams to optimize memory usage.
    Appends a unique UUID to prevent file name collisions.
    Returns the newly generated unique filename.
    """
    # 1. Clean the original filename and extract its extension
    original_name = upload_file.filename
    ext = os.path.splitext(original_name)[1]
    
    # 2. Generate a completely unique filename using UUID4
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    # 3. Stream the file directly to disk in chunks to keep memory usage minimal
    try:
        with open(file_path, "wb") as buffer:
            # shutil.copyfileobj streams data without loading the whole file into RAM
            shutil.copyfileobj(upload_file.file, buffer)
    finally:
        # Always clean up and close the FastAPI internal file spooler
        upload_file.file.close()
        
    return unique_filename