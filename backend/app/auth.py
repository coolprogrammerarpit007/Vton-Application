import os
import secrets
import random
import smtplib
import logging
from pydantic import EmailStr
from datetime import datetime, timedelta
from email.message import EmailMessage
from logging.handlers import TimedRotatingFileHandler

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import JWTError, jwt
from typing import Optional

# Import your database models, schemas, configs, and session dependency
from . import models
from .schemas import (
    UserCreate, UserLogin, StandardResponse,
    ForgotPasswordRequest, VerifyOTPRequest, ResetPasswordRequest, GoogleLoginPayload
)
from .database import get_db
from .config import settings  # ADDED: Import settings for dynamic backend URL
from app.exceptions import APIException

# --- Security Configuration ---
load_dotenv() 

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("No JWT_SECRET_KEY set in .env file")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 Days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# --- Logging Configuration (Daily Rotating) ---
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    file_handler = TimedRotatingFileHandler(
        filename="logs/auth.log",
        when="midnight",    
        interval=1,         
        backupCount=30,     
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


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
    credentials_exception = APIException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        msg="Could not validate credentials"
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        
        if user_id is None:
            logger.warning("Token validation failed: Missing user_id (sub) in payload")
            raise credentials_exception
            
    except JWTError as e:
        logger.warning(f"Token validation failed: JWT decoding error ({str(e)})")
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    
    if user is None:
        logger.warning(f"Token validation failed: User ID {user_id} not found in database")
        raise credentials_exception
        
    return user

# Create a non-blocking OAuth2 scheme
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional), 
    db: Session = Depends(get_db)
) -> Optional[models.User]:
    """
    Attempts to fetch the user if a token is provided. 
    Returns None if no token exists or if the token is invalid (public user).
    """
    if not token:
        return None 

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        
        if user_id is None:
            return None
            
    except JWTError:
        # Fails gracefully without throwing an APIException
        return None 
        
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    return user


# --- API Routes ---

@router.get("/me", response_model=StandardResponse)
def get_me(current_user: models.User = Depends(get_current_user)):
    return StandardResponse(
        status=True,
        msg="User details fetched successfully!",
        data={
            "user_id":current_user.id,
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
    
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        logger.warning(f"Registration failed: Email {user.email} is already registered")
        raise APIException(status_code=400, msg="Email is already registered")
    
    try:
        if len(user.password.encode("utf-8")) > 72:
            raise APIException(status_code=400, msg="Password is too long (max 72 characters)")

        hashed_pw = get_password_hash(user.password)
        new_user = models.User(
            username=user.username,
            full_name=user.username,  # FIX: Populate admin full_name field
            email=user.email,
            password=hashed_pw,       # FIX: Populate admin password field securely
            hashed_password=hashed_pw,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        logger.info(f"User registered successfully: {new_user.username} (ID: {new_user.id})")
        access_token = create_access_token(data={"sub": str(new_user.id)})
        
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
        raise
    except Exception as e:
        logger.error(f"Error during user registration for {user.email}: {str(e)}")
        db.rollback()
        raise APIException(status_code=500, msg="Internal server error during registration")
    
    
@router.post("/login-via-google", response_model=StandardResponse)
def google_auth_login(payload: GoogleLoginPayload, db: Session = Depends(get_db)):
    try:
        if not payload.email or not payload.email_verified:
            raise APIException(status_code=200, msg="Unverified or missing Google email address.")
        if not payload.sub:
            raise APIException(status_code=200, msg="Missing Google security identifier (sub).")

        db_user = db.query(models.User).filter(models.User.google_sub == payload.sub).first()

        if not db_user:
            db_user = db.query(models.User).filter(models.User.email == payload.email).first()
            
            if db_user:
                db_user.google_sub = payload.sub
                db_user.auth_provider = "google"
                
                if not getattr(db_user, 'avatar_url', None) and payload.picture:
                    db_user.avatar_url = payload.picture
                    
                db.commit()
                logger.info(f"Linked Google sub to existing user: {db_user.email} (ID: {db_user.id})")
            else:
                db_user = models.User(
                    username=payload.name,
                    full_name=payload.name,            # FIX: Populate admin full_name field
                    email=payload.email,
                    password="GOOGLE_OAUTH_ACCOUNT",   # FIX: Provide fallback to satisfy NOT NULL constraints
                    hashed_password=None,
                    auth_provider="google",
                    google_sub=payload.sub,  
                    avatar_url=payload.picture
                )
                db.add(db_user)
                db.commit()
                db.refresh(db_user)
                logger.info(f"New user registered via Google sub: {db_user.email} (ID: {db_user.id})")
        else:
            logger.info(f"Existing user logged in via Google sub: {db_user.email} (ID: {db_user.id})")
            if not getattr(db_user, 'avatar_url', None) and payload.picture:
                db_user.avatar_url = payload.picture
                db.commit()
                db.refresh(db_user)
                logger.info(f"Updated missing avatar_url for existing user: {db_user.email}")

        access_token = create_access_token(data={"sub": str(db_user.id)})
        avatar_path = getattr(db_user, 'avatar_url', None)
        
        # UPDATED: Pull the base URL dynamically from config instead of a hardcoded string
        base_url = settings.BACKEND_URL.rstrip("/")
        
        if avatar_path and not avatar_path.startswith("http"):
            avatar_path = f"{base_url}/{avatar_path}"
        
        return StandardResponse(
            status=True,
            msg="Successfully authenticated via Google",
            data={
                "access_token": access_token, 
                "token_type": "bearer",
                "user": {
                    "id": db_user.id,
                    "username": db_user.username,
                    "email": db_user.email,
                    "picture": payload.picture,
                    "avatar_url": avatar_path,
                    "provider": db_user.auth_provider
                }
            }
        )

    except APIException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Google Auth Error: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Internal server error processing Google login.")


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
        logger.error(f"Critical error during logout for user {current_user.id}: {str(e)}")
        raise APIException(status_code=500, msg="Internal server error during logout.")


# --- Password Reset Flow ---

def generate_otp() -> str:
    """Generates a secure 6-digit numeric OTP."""
    return f"{random.randint(100000, 999999)}"

def send_otp_email(to_email: str, otp: str):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    mail_from = os.getenv("MAIL_FROM", smtp_user)

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
    
    
@router.post("/forgot-password", response_model=StandardResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    logger.info(f"Password reset requested for email: {payload.email}")

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        logger.warning(f"Forgot password attempt for non-existent email: {payload.email}")
        raise APIException(status_code=200, msg="No account found with this email address.")

    try:
        db.query(models.PasswordResetOTP).filter(
            models.PasswordResetOTP.user_id == user.id,
            models.PasswordResetOTP.is_used == False
        ).update({"is_used": True})

        otp_code = generate_otp()
        created_time = datetime.utcnow() + timedelta(hours=5.5)
        expiry_time = created_time + timedelta(minutes=10)

        otp_record = models.PasswordResetOTP(
            user_id=user.id,
            otp=otp_code,
            expires_at=expiry_time,
            created_at=created_time,
            updated_at=created_time,
            is_used=False
        )
        db.add(otp_record)
        db.commit()

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
    
    
@router.post("/verify-otp", response_model=StandardResponse)
def verify_otp(payload: VerifyOTPRequest, db: Session = Depends(get_db)):
    logger.info(f"Verifying password reset OTP for email: {payload.email}")

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise APIException(status_code=404, msg="User not found.")

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
        reset_token = secrets.token_urlsafe(32)
        
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
    
    
@router.post("/reset-password", response_model=StandardResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    logger.info(f"Resetting password for email: {payload.email}")

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise APIException(status_code=404, msg="User not found.")

    token_record = db.query(models.PasswordResetOTP).filter(
        models.PasswordResetOTP.user_id == user.id,
        models.PasswordResetOTP.reset_token == payload.reset_token,
        models.PasswordResetOTP.is_used == True
    ).first()

    if not token_record:
        logger.warning(f"Reset password failed for {payload.email}: Invalid or reused reset token")
        raise APIException(status_code=400, msg="Invalid or expired reset token.")

    if datetime.utcnow() > token_record.expires_at + timedelta(minutes=15):
        raise APIException(status_code=400, msg="Reset session expired. Please restart the process.")

    try:
        if len(payload.new_password.encode("utf-8")) > 72:
            raise APIException(status_code=400, msg="Password is too long (max 72 characters)")

        new_hashed = get_password_hash(payload.new_password)
        user.hashed_password = new_hashed
        user.password = new_hashed
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