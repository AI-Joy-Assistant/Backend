from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from config.settings import settings
from src.auth.auth_router import router as auth_router
from src.chat.chat_router import router as chat_router
from src.friends.friends_router import router as friends_router
from src.calendar.calender_router import router as calendar_router
from src.a2a.a2a_router import router as a2a_router
from src.intent.router import router as intent_router
from src.websocket.websocket_manager import manager as ws_manager
import logging

# httpx (Supabase 통신) 로그 숨기기
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# uvicorn 접속 로그 (GET /chat/history ... 200 OK) 숨기기
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
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
    expose_headers=["*"],
)

# 라우터 등록
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(friends_router)
app.include_router(calendar_router)
app.include_router(a2a_router)
app.include_router(intent_router)

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


# WebSocket 엔드포인트 - 실시간 알림용
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """WebSocket 연결 - 실시간 알림 수신"""
    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            # 클라이언트로부터 메시지 수신 (ping/pong 또는 앱 상태 업데이트용)
            data = await websocket.receive_text()
            # 필요시 처리 (예: 읽음 확인 등)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True
    ) 
