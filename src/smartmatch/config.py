import logging
import sys
from dotenv import find_dotenv, load_dotenv
from typing import Dict, List, Any, Optional, Self
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, ValidationInfo, model_validator

# Ensure .env is loaded before settings are initialized
load_dotenv()

# Set up dedicated structural logger
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Production-grade centralized application configuration schema.
    """
    model_config = SettingsConfigDict(
        env_file=find_dotenv(),
        env_file_encoding='utf-8',
        extra='ignore'
    )

    # ==========================================
    # 1. CORE TELEGRAM INTEGRATION ENGINE
    # ==========================================
    TELEGRAM_TOKEN: str = Field(..., description="Active cryptographic bot access token")
    TELEGRAM_WEBHOOK_SECRET: str = Field(..., description="Cryptographic secret header token for webhook verification")
    API_ID: Optional[int] = Field(None, description="Telegram Client API ID")
    API_HASH: Optional[str] = Field(None, description="Telegram Client API hash")
    TELETHON_SESSION_STRING: Optional[str] = Field(None, description="Telethon string session")

    # ==========================================
    # 2. PRIVILEGED ACCESS
    # ==========================================
    TRACKER_PHONE_NUMBER: str = Field("+251922707554", description="Tracking target phone")
    ADMINISTRATOR_PHONE_NUMBERS: List[str] = Field(
        default_factory=lambda: ["+251912227516", "+251922707554"],
        description="List of administrative phone entities"
    )

    # ==========================================
    # 3. AI & FINANCIAL ENGINES
    # ==========================================
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API key")
    CHAPA_SECRET_KEY: str = Field(..., description="Chapa secret API key")
    CHAPA_WEBHOOK_SECRET: str = Field(..., description="Chapa webhook secret")
    CHAPA_ENCRPTION_KEY: Optional[str] = Field(None, description="Direct Charge encryption key")

    # ==========================================
    # 4. SUPABASE CLOUD INFRASTRUCTURE
    # ==========================================
    SUPABASE_URL: str = Field(..., description="Supabase REST API URL")
    SUPABASE_ANON_KEY: str = Field(..., description="Supabase public/anon key")
    SERVICE_ROLE_KEY: str = Field(..., description="Supabase service role key")
    SUPABASE_SERVICE_KEY: str = Field(..., description="Alias for service role key")

    @property
    def SUPABASE_SERVICE_ROLE_KEY(self) -> str:
        return self.SERVICE_ROLE_KEY

    # ==========================================
    # 5. DATABASE & STORAGE
    # ==========================================
    DATABASE_URL: str = Field(..., description="PostgreSQL/PostGIS connection string")
    REDIS_URL: str = Field(..., description="Redis connection URI")
    B2_KEY_ID: str = Field(..., description="Backblaze B2 Key ID")
    B2_APPLICATION_KEY: str = Field(..., description="Backblaze B2 Application Key")
    S3_BUCKET_NAME: str = Field(..., description="Target S3-compatible bucket name")
    B2_ENDPOINT_URL: str = Field(..., description="B2 API endpoint URL")

    # ==========================================
    # 6. RUNTIME CONFIGURATION (Hugging Face Optimized)
    # ==========================================
    DEBUG_MODE: bool = Field(False)
    SERVER_HOST: str = Field("0.0.0.0")
    SERVER_WORKERS: int = Field(1, ge=1, le=2)
    SERVER_PORT: int = Field(10000, description="Hugging Face Spaces requires port 7860")
    WORKER_POOL_SIZE: int = Field(2, ge=1, le=5)
    PRODUCTION_API_URL: str = Field(
        ...,
        description="Root URL of production deployment (e.g., https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space)"
    )

    # =========================================================================
    # VALIDATORS
    # =========================================================================

    @model_validator(mode='after')
    def validate_secrets_not_empty(self) -> Self:
        """
        Validates that critical secrets are present and not placeholders.
        """
        critical_fields = {
            "TELEGRAM_TOKEN": self.TELEGRAM_TOKEN,
            "TELEGRAM_WEBHOOK_SECRET": self.TELEGRAM_WEBHOOK_SECRET,
            "GEMINI_API_KEY": self.GEMINI_API_KEY,
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_ANON_KEY": self.SUPABASE_ANON_KEY,
            "SERVICE_ROLE_KEY": self.SERVICE_ROLE_KEY,
            "CHAPA_SECRET_KEY": self.CHAPA_SECRET_KEY
        }

        for name, value in critical_fields.items():
            if not value or value.strip() in ["your_key_here", "place_holder", ""]:
                raise ValueError(f"The configuration variable '{name}' is missing or set to an insecure placeholder.")
        return self

    @field_validator("TRACKER_PHONE_NUMBER")
    @classmethod
    def validate_phone_format(cls, value: str) -> str:
        cleaned = value.replace(" ", "").strip()
        if not cleaned.startswith("+"):
            raise ValueError("Phone numbers must use international E.164 format (starting with +).")
        return cleaned

    @field_validator("ADMINISTRATOR_PHONE_NUMBERS", mode="before")
    @classmethod
    def parse_and_normalize_admin_list(cls, value: Any) -> List[str]:
        if isinstance(value, str):
            normalized = value.replace(" or ", ",").replace(";", ",").split(",")
            return [phone.strip() for phone in normalized if phone.strip()]
        return value

    @field_validator("CHAPA_SECRET_KEY")
    @classmethod
    def validate_chapa_key(cls, value: str) -> str:
        if not value.startswith(("CHASECK_TEST-", "CHASECK_PROD-", "CHASECK_")):
            logger.warning("⚠️ CHAPA_SECRET_KEY prefix verification failed.")
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def convert_postgres_schema(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned.startswith("postgres://"):
            return cleaned.replace("postgres://", "postgresql+asyncpg://", 1)
        return cleaned

    @field_validator("SERVER_WORKERS", "WORKER_POOL_SIZE")
    @classmethod
    def enforce_runtime_limits(cls, value: int, info: ValidationInfo) -> int:
        limits = {"SERVER_WORKERS": 2, "WORKER_POOL_SIZE": 4}
        if value > limits.get(info.field_name, 2):
            logger.warning(f"🚨 {info.field_name} over-provisioned. Scaling down.")
            return limits.get(info.field_name, 2)
        return value

    def get_masked_config(self) -> Dict[str, Any]:
        config_dict = self.model_dump()
        sensitive_keys = [
            "TELEGRAM_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "API_HASH", "GEMINI_API_KEY",
            "CHAPA_SECRET_KEY", "CHAPA_WEBHOOK_SECRET", "DATABASE_URL",
            "SUPABASE_SERVICE_KEY", "SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"
        ]
        for key in sensitive_keys:
            if key in config_dict and config_dict[key]:
                val_str = str(config_dict[key])
                config_dict[key] = f"{val_str[:4]}...********...{val_str[-4:]}"
        return config_dict


# Initialization
try:
    settings = Settings()
    logger.info("⚙️ Centralized application settings parsed successfully.")
except Exception as init_error:
    logger.critical(f"🚨 CRITICAL CONFIGURATION FAILURE: {str(init_error)}")
    sys.exit("System failed to boot: Critical configuration parameters missing.")
