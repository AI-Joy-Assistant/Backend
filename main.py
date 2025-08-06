from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from config.settings import settings
from src.auth.router import router as auth_router
from src.chat.router import router as chat_router
from src.friends.router import router as friends_router
from src.calendar.router import router as calendar_router

# FastAPI 애플리케이션 생성
app = FastAPI(
    title="AI Joy Assistant Backend API",
    version="1.0.0",
    description="백엔드 API - Python FastAPI 버전",
    docs_url="/api-docs",
    redoc_url="/redoc"
)

# 세션 미들웨어 설정 (CORS보다 먼저 설정)
app.add_middleware(
    SessionMiddleware, 
    secret_key="your-secret-key-for-session"  # 실제로는 환경변수로 관리
)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(friends_router)
app.include_router(calendar_router)

@app.get("/")
async def root():
    return {"message": "AI Joy Assistant Backend API v1.0.0"}

@app.get("/debug")
async def debug():
    from config.settings import settings
    import os
    
    # .env 파일 존재 여부 확인
    env_file_exists = os.path.exists(".env")
    
    return {
        "env_file_exists": env_file_exists,
        "settings_status": {
            "jwt_secret_set": bool(settings.JWT_SECRET and len(settings.JWT_SECRET) > 10),
            "google_client_id_set": bool(settings.GOOGLE_CLIENT_ID and not settings.GOOGLE_CLIENT_ID.startswith("your")),
            "google_client_secret_set": bool(settings.GOOGLE_CLIENT_SECRET and not settings.GOOGLE_CLIENT_SECRET.startswith("your")),
            "supabase_url_set": bool(settings.SUPABASE_URL and not settings.SUPABASE_URL.startswith("https://your")),
            "supabase_key_set": bool(settings.SUPABASE_SERVICE_KEY and not settings.SUPABASE_SERVICE_KEY.startswith("your"))
        },
        "supabase_url": settings.SUPABASE_URL if settings.SUPABASE_URL else "NOT_SET",
        "google_redirect_uri": settings.GOOGLE_REDIRECT_URI if settings.GOOGLE_REDIRECT_URI else "NOT_SET"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True
    ) 