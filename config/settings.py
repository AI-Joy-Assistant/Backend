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
    BASE_URL: str = "http://localhost:8000"  # 웹훅 URL용
    
    # JWT 설정
    JWT_SECRET: str = "PLEASE_SET_JWT_SECRET_IN_ENV_FILE"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 1
    
    # Google OAuth 설정
    GOOGLE_CLIENT_ID: str = "PLEASE_SET_GOOGLE_CLIENT_ID_IN_ENV_FILE"
    GOOGLE_CLIENT_SECRET: str = "PLEASE_SET_GOOGLE_CLIENT_SECRET_IN_ENV_FILE"
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"
    
    # Apple OAuth 설정
    APPLE_CLIENT_ID: str = "com.joyner.app"  # Bundle ID
    APPLE_TEAM_ID: str = "36TRH8W787"
    APPLE_KEY_ID: str = "N3Q9P2S887"
    APPLE_PRIVATE_KEY: str = ""  # .p8 파일 내용 (환경변수로 설정)
    
    # Supabase 설정
    SUPABASE_URL: str = "PLEASE_SET_SUPABASE_URL_IN_ENV_FILE"
    SUPABASE_SERVICE_KEY: str = "PLEASE_SET_SUPABASE_SERVICE_KEY_IN_ENV_FILE"
    
    # LLM 설정 (Llama API 우선, OpenAI는 폴백)
    LLM_API_URL: Optional[str] = None  # Llama API URL (설정 시 OpenAI 대신 사용)
    LLM_API_KEY: Optional[str] = None  # Llama API Key (인증이 필요한 경우)
    OPENAI_API_KEY: str = "PLEASE_SET_OPENAI_API_KEY_IN_ENV_FILE"
    OPENAI_MODEL: str = "gpt-4"  # gpt-4, gpt-4-turbo, gpt-4o, gpt-4o-mini 중 선택
    
    # CORS 설정
    CORS_ORIGINS: list[str] = [
         "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:19006",
    "http://127.0.0.1:19006",# Expo 웹 대체 포트
    ]
    CORS_CREDENTIALS: bool = True

settings = Settings() 