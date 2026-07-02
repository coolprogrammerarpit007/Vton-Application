import os
import logging
from logging.handlers import TimedRotatingFileHandler
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt
from dotenv import load_dotenv

# Import your database models and session dependency
from . import models
from .database import get_db

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
router = APIRouter(prefix="/api/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# --- Pydantic Schemas ---
class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
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


@router.get("/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    return {"username": current_user.username, "email": current_user.email}


@router.post("/register", response_model=Token)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    # logger.info(f"DEBUG: Password received is {len(user.password)} characters long. First 5 chars: {user.password[:5]}")
    logger.info(f"Registration attempt for email: {user.email}")
    
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        logger.warning(f"Registration failed: Email {user.email} is already registered")
        raise HTTPException(status_code=400, detail="Email already registered")
    
    try:
        
        if len(user.password.encode("utf-8")) > 72:
            raise HTTPException(
                status_code=400,
                detail="Password must not exceed 72 bytes."
            )

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
        return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Error during user registration for {user.email}: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error during registration")

@router.post("/login", response_model=Token)
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    logger.info(f"Login attempt for email: {user.email}")
    
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user:
        logger.warning(f"Login failed: No account found for email {user.email}")
        raise HTTPException(status_code=404, detail="Invalid credentials")
    
    if not verify_password(user.password, db_user.hashed_password):
        logger.warning(f"Login failed: Incorrect password for email {user.email}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    logger.info(f"User logged in successfully: {db_user.username} (ID: {db_user.id})")
    access_token = create_access_token(data={"sub": str(db_user.id)})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
def logout_user(current_user: models.User = Depends(get_current_user)):
    # In a stateless JWT system, logout is primarily a frontend action (clearing the token).
    # We log it here for security auditing.
    logger.info(f"User logged out: {current_user.username} (ID: {current_user.id})")
    return {"message": "Logged out successfully"}