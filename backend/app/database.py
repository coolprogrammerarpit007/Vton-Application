from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

# 1. Create the MySQL engine connection using the dynamic URL from config
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# 2. Create a thread-safe session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. Base class for database models
Base = declarative_base()

# 4. Dependency to inject DB sessions into FastAPI routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()