from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

class Settings(BaseSettings):
    DB_CONNECTION: str = "mysql"
    DB_HOST: str = "localhost"
    DB_PORT: int = 3307
    DB_DATABASE: str = "vton_db"
    DB_USERNAME: str = "root"
    DB_PASSWORD: str = ""
    FASHN_API_KEY: str
    
    # --- PayU Configuration ---
    PAYU_MERCHANT_KEY: str = "cSL3Sa"
    PAYU_MERCHANT_SALT: str = "xsDXTQ37nAacgtDh8gcKiybvKK7EqaLW"
    PAYU_BASE_URL: str = "https://test.payu.in/_payment" # Change to https://secure.payu.in/_payment in production
    
    # --- App URLs ---
    BACKEND_URL: str = "http://127.0.0.1:8000"
    FRONTEND_URL: str = "http://127.0.0.1:5500"


    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        # Automatically builds: mysql+pymysql://root:password@localhost:3307/vton_db
        return f"mysql+pymysql://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()