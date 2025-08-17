from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # 추가 환경변수 무시
    )
    
    # 서버 설정
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    BASE_URL: str = "http://localhost:3000"  # 웹훅 URL용
    
    # JWT 설정
    JWT_SECRET: str = "PLEASE_SET_JWT_SECRET_IN_ENV_FILE"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 1
    
    # Google OAuth 설정
    GOOGLE_CLIENT_ID: str = "PLEASE_SET_GOOGLE_CLIENT_ID_IN_ENV_FILE"
    GOOGLE_CLIENT_SECRET: str = "PLEASE_SET_GOOGLE_CLIENT_SECRET_IN_ENV_FILE"
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"
    
    # Supabase 설정
    SUPABASE_URL: str = "PLEASE_SET_SUPABASE_URL_IN_ENV_FILE"
    SUPABASE_SERVICE_KEY: str = "PLEASE_SET_SUPABASE_SERVICE_KEY_IN_ENV_FILE"
    
    # OpenAI 설정
    OPENAI_API_KEY: str = "PLEASE_SET_OPENAI_API_KEY_IN_ENV_FILE"
    OPENAI_MODEL: str = "gpt-4"  # gpt-4, gpt-4-turbo, gpt-4o, gpt-4o-mini 중 선택
    
    # CORS 설정
    CORS_ORIGINS: list[str] = [
         "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:19006",
    "http://127.0.0.1:19006",# Expo 웹 대체 포트
    ]
    CORS_CREDENTIALS: bool = True

settings = Settings() 