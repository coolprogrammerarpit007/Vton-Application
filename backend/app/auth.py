import secrets
import random
import smtplib
from email.message import EmailMessage
from fastapi import APIRouter, Depends, status
from pydantic import EmailStr
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

# Import your database models and session dependency
from . import models
from .schemas import (
    UserCreate, UserLogin, StandardResponse,
    ForgotPasswordRequest, VerifyOTPRequest, ResetPasswordRequest
)
from .database import get_db
from app.exceptions import APIException

import os
from dotenv import load_dotenv
import logging
from logging.handlers import TimedRotatingFileHandler
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from jose import JWTError, jwt



# --- Logging Configuration (Daily Rotating) ---
# 1. Ensure a 'logs' directory exists on your server
os.makedirs("logs", exist_ok=True)

# 2. Set up the logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Set base level to INFO

# Prevent adding handlers multiple times if the module reloads
if not logger.handlers:
    # 3. Create the log format
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # 4. Console Handler (Keeps logs visible in your terminal / systemctl status)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 5. Daily File Handler (Generates date-wise logs)
    # This writes to logs/auth.log today, then renames it to auth.log.YYYY-MM-DD at midnight
    file_handler = TimedRotatingFileHandler(
        filename="logs/auth.log",
        when="midnight",    # Rotate the file every night at midnight
        interval=1,         # Every 1 day
        backupCount=30,     # Keep the last 30 days of logs, delete older ones automatically
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # 6. Attach both handlers to your logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

# --- Security Configuration ---
# Load environment variables from your .env file
load_dotenv() 

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    logger.critical("Failed to start: JWT_SECRET_KEY is missing from .env file!")
    raise ValueError("No JWT_SECRET_KEY set in .env file")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 Days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter(prefix="/api/auth")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# --- Pydantic Schemas ---

    

# --- Utility Functions ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    logger.debug(f"Created new access token for user ID: {data.get('sub')}")
    return encoded_jwt

# --- Route Protection Dependency ---
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    # Initialize the custom exception using 'msg'
    credentials_exception = APIException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        msg="Could not validate credentials"
    )
    
    try:
        # 1. Decode the token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        
        if user_id is None:
            logger.warning("Token validation failed: Missing user_id (sub) in payload")
            raise credentials_exception
            
    except JWTError as e:
        logger.warning(f"Token validation failed: JWT decoding error ({str(e)})")
        raise credentials_exception
        
    # 2. Grab the user from the database
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    
    if user is None:
        logger.warning(f"Token validation failed: User ID {user_id} not found in database")
        raise credentials_exception
        
    return user


# --- API Routes ---

# Move response_model up into the route decorator
@router.get("/me", response_model=StandardResponse)
def get_me(current_user: models.User = Depends(get_current_user)):
    return StandardResponse(
        status=True,
        msg="User details fetched successfully!",
        data={
           "username": current_user.username, 
           "email": current_user.email
        }
    )
    
    


@router.post("/login", response_model=StandardResponse)
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    logger.info(f"Login attempt for email: {user.email}")
    
    try:
        db_user = db.query(models.User).filter(models.User.email == user.email).first()
        
        if not db_user:
            logger.warning(f"Login failed: No account found for email {user.email}")
            raise APIException(status_code=401, msg="Invalid credentials")
        
        if not verify_password(user.password, db_user.hashed_password):
            logger.warning(f"Login failed: Incorrect password for email {user.email}")
            raise APIException(status_code=401, msg="Invalid credentials")
        
        logger.info(f"User logged in successfully: {db_user.username} (ID: {db_user.id})")
        access_token = create_access_token(data={"sub": str(db_user.id)})
        
        # CHANGED: Added user details to the response data payload
        return StandardResponse(
            status=True,
            msg="User logged in successfully",
            data={
                "access_token": access_token, 
                "token_type": "bearer",
                "user": {
                    "id": db_user.id,
                    "username": db_user.username,
                    "email": db_user.email
                }
            }
        )
        
    except APIException:
        raise
    except Exception as e:
        logger.error(f"Critical error during login for {user.email}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error. Please try again later.")


@router.post("/register", response_model=StandardResponse)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    logger.info(f"Registration attempt for email: {user.email}")
    
    # 1. Business Logic Validation: Check if email exists
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        logger.warning(f"Registration failed: Email {user.email} is already registered")
        raise APIException(status_code=400, msg="Email is already registered")
    
    try:
        # 2. Tech Logic Validation: Bcrypt 72-byte limit
        if len(user.password.encode("utf-8")) > 72:
            raise APIException(status_code=400, msg="Password is too long (max 72 characters)")

        # 3. Create User
        hashed_pw = get_password_hash(user.password)
        new_user = models.User(
            username=user.username,
            email=user.email,
            hashed_password=hashed_pw
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        logger.info(f"User registered successfully: {new_user.username} (ID: {new_user.id})")
        access_token = create_access_token(data={"sub": str(new_user.id)})
        
       # CHANGED: Added user details to the response data payload
        return StandardResponse(
            status=True,
            msg="New User Registered successfully",
            data={
                "access_token": access_token, 
                "token_type": "bearer",
                "user": {
                    "id": new_user.id,
                    "username": new_user.username,
                    "email": new_user.email
                }
            }
        )
        
    except APIException:
        #THIS: Let our custom exceptions pass through untouched
        raise
        
    
    except Exception as e:
        logger.error(f"Error during user registration for {user.email}: {str(e)}")
        db.rollback()
        raise APIException(status_code=500, msg="Internal server error during registration")



@router.post("/logout", response_model=StandardResponse)
def logout_user(current_user: models.User = Depends(get_current_user)):
    try:
        logger.info(f"User logged out: {current_user.username} (ID: {current_user.id})")
        
        return StandardResponse(
            status=True,
            msg="Logged out successfully",
            data=None
        )
        
    except Exception as e:
        # Catch unexpected server errors (e.g., if your logger fails or a DB connection drops)
        logger.error(f"Critical error during logout for user {current_user.id}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error during logout.")
    
    
    
    
    
    
    
# *************************************  API development for the forgot password ****************

# Helper function for OTP and Email

def generate_otp() -> str:
    """Generates a secure 6-digit numeric OTP."""
    return f"{random.randint(100000, 999999)}"


def send_otp_email(to_email: str, otp: str):
    """
    Sends the OTP via SMTP. Configure these ENV variables in your .env file:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    mail_from = os.getenv("MAIL_FROM", smtp_user)

    # If SMTP is not configured in development, log the OTP for testing
    if not smtp_user or not smtp_password:
        logger.warning(f"[DEV MODE] SMTP credentials missing. Generated OTP for {to_email}: {otp}")
        return

    msg = EmailMessage()
    msg["Subject"] = "Password Reset Request - Your OTP Code"
    msg["From"] = mail_from
    msg["To"] = to_email
    msg.set_content(
        f"Hello,\n\n"
        f"Your One-Time Password (OTP) to reset your password is: {otp}\n\n"
        f"This code will expire in 10 minutes. If you did not request this, please ignore this email.\n\n"
        f"Regards,\nSecurity Team"
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info(f"Password reset OTP successfully sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        raise APIException(status_code=500, msg="Failed to send OTP email. Please try again later.")
    
    
    
# ==========================================
# 1. FORGOT PASSWORD API
# ==========================================
@router.post("/forgot-password", response_model=StandardResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    logger.info(f"Password reset requested for email: {payload.email}")

    # 1. Verify user exists
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        logger.warning(f"Forgot password attempt for non-existent email: {payload.email}")
        raise APIException(status_code=200, msg="No account found with this email address.")

    try:
        # 2. Invalidate any existing unused OTPs for this user
        db.query(models.PasswordResetOTP).filter(
            models.PasswordResetOTP.user_id == user.id,
            models.PasswordResetOTP.is_used == False
        ).update({"is_used": True})

        # 3. Generate new OTP (Expires in 10 Minutes)
        otp_code = generate_otp()
        expiry_time = datetime.utcnow() + timedelta(minutes=10)

        otp_record = models.PasswordResetOTP(
            user_id=user.id,
            otp=otp_code,
            expires_at=expiry_time,
            is_used=False
        )
        db.add(otp_record)
        db.commit()

        # 4. Dispatch Email
        send_otp_email(to_email=user.email, otp=otp_code)

        return StandardResponse(
            status=True,
            msg="OTP code sent successfully to your registered email address.",
            data={"email": user.email}
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error during forgot password for {payload.email}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error generating reset token.")
    
    
    
# ==========================================
# 2. VERIFY OTP API
# ==========================================
@router.post("/verify-otp", response_model=StandardResponse)
def verify_otp(payload: VerifyOTPRequest, db: Session = Depends(get_db)):
    logger.info(f"Verifying password reset OTP for email: {payload.email}")

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise APIException(status_code=404, msg="User not found.")

    # Fetch latest active OTP record
    otp_record = db.query(models.PasswordResetOTP).filter(
        models.PasswordResetOTP.user_id == user.id,
        models.PasswordResetOTP.otp == payload.otp,
        models.PasswordResetOTP.is_used == False
    ).order_by(models.PasswordResetOTP.created_at.desc()).first()

    if not otp_record:
        logger.warning(f"OTP verification failed for {payload.email}: Invalid code")
        raise APIException(status_code=400, msg="Invalid OTP code.")

    if datetime.utcnow() > otp_record.expires_at:
        logger.warning(f"OTP verification failed for {payload.email}: Code expired")
        raise APIException(status_code=400, msg="OTP code has expired. Please request a new one.")

    try:
        # Generate a secure single-use reset token for step 3
        reset_token = secrets.token_urlsafe(32)
        
        # Mark OTP as verified and store reset_token
        otp_record.is_used = True
        otp_record.reset_token = reset_token
        db.commit()

        logger.info(f"OTP successfully verified for user ID {user.id}")

        return StandardResponse(
            status=True,
            msg="OTP verified successfully. You may now reset your password.",
            data={
                "email": user.email,
                "reset_token": reset_token
            }
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error verifying OTP for {payload.email}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error verifying OTP.")
    
    
    
# ==========================================
# 3. RESET PASSWORD API
# ==========================================
@router.post("/reset-password", response_model=StandardResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    logger.info(f"Resetting password for email: {payload.email}")

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise APIException(status_code=404, msg="User not found.")

    # Validate reset token
    token_record = db.query(models.PasswordResetOTP).filter(
        models.PasswordResetOTP.user_id == user.id,
        models.PasswordResetOTP.reset_token == payload.reset_token,
        models.PasswordResetOTP.is_used == True
    ).first()

    if not token_record:
        logger.warning(f"Reset password failed for {payload.email}: Invalid or reused reset token")
        raise APIException(status_code=400, msg="Invalid or expired reset token.")

    # Enforce 15-minute validity window for password reset after verification
    if datetime.utcnow() > token_record.expires_at + timedelta(minutes=15):
        raise APIException(status_code=400, msg="Reset session expired. Please restart the process.")

    try:
        # Tech Logic Validation: Bcrypt 72-byte limit
        if len(payload.new_password.encode("utf-8")) > 72:
            raise APIException(status_code=400, msg="Password is too long (max 72 characters)")

        # Hash new password & update
        user.hashed_password = get_password_hash(payload.new_password)
        
        # Invalidate reset token so it cannot be used again
        token_record.reset_token = None
        db.commit()

        logger.info(f"Password successfully updated for user ID: {user.id}")

        return StandardResponse(
            status=True,
            msg="Password updated successfully! You can now log in with your new password.",
            data=None
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting password for {payload.email}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error resetting password.")