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

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        # Automatically builds: mysql+pymysql://root:password@localhost:3307/vton_db
        return f"mysql+pymysql://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()