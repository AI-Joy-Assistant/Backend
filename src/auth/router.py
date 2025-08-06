from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import RedirectResponse
from typing import Optional
from .models import UserCreate, UserLogin, UserResponse, TokenResponse
from .service import AuthService
from .repository import AuthRepository
from config.database import get_supabase_client

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate):
    """사용자 회원가입"""
    try:
        supabase = get_supabase_client()
        auth_repo = AuthRepository(supabase)
        auth_service = AuthService(auth_repo)
        
        user = await auth_service.register_user(user_data)
        return user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login", response_model=TokenResponse)
async def login(user_data: UserLogin):
    """사용자 로그인"""
    try:
        supabase = get_supabase_client()
        auth_repo = AuthRepository(supabase)
        auth_service = AuthService(auth_repo)
        
        token = await auth_service.login_user(user_data)
        return token
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.get("/google")
async def google_auth():
    """Google OAuth 인증 시작"""
    from config.settings import settings
    
    # Google OAuth URL 생성
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={settings.GOOGLE_CLIENT_ID}&"
        f"redirect_uri={settings.GOOGLE_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=openid%20email%20profile&"
        f"access_type=offline"
    )
    
    return RedirectResponse(url=auth_url)

@router.get("/google/callback")
async def google_auth_callback(code: str, request: Request):
    """Google OAuth 콜백 처리"""
    try:
        from config.settings import settings
        import httpx
        
        # 액세스 토큰 교환
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        }
        
        async with httpx.AsyncClient() as client:
            token_response = await client.post(token_url, data=token_data)
            token_response.raise_for_status()
            tokens = token_response.json()
        
        # 사용자 정보 가져오기
        user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        async with httpx.AsyncClient() as client:
            user_response = await client.get(user_info_url, headers=headers)
            user_response.raise_for_status()
            user_info = user_response.json()
        
        # Supabase에 사용자 정보 저장 또는 업데이트
        supabase = get_supabase_client()
        auth_repo = AuthRepository(supabase)
        auth_service = AuthService(auth_repo)
        
        # Google 사용자 정보로 회원가입/로그인 처리
        user_data = UserCreate(
            email=user_info["email"],
            password="",  # Google OAuth는 비밀번호가 없음
            name=user_info.get("name", ""),
            google_id=user_info["id"]
        )
        
        try:
            # 기존 사용자인지 확인하고 로그인
            login_data = UserLogin(email=user_info["email"], password="")
            token = await auth_service.login_google_user(user_info)
        except:
            # 새 사용자라면 회원가입
            user = await auth_service.register_google_user(user_data)
            token = await auth_service.login_google_user(user_info)
        
        # 프론트엔드로 리다이렉트 (토큰 포함)
        frontend_url = "http://localhost:8081"  # Expo 웹 개발 서버
        redirect_url = f"{frontend_url}?token={token.access_token}"
        
        return RedirectResponse(url=redirect_url)
        
    except Exception as e:
        # 에러 발생 시 프론트엔드로 에러와 함께 리다이렉트
        frontend_url = "http://localhost:8081"
        error_url = f"{frontend_url}?error={str(e)}"
        return RedirectResponse(url=error_url)

@router.get("/me", response_model=UserResponse)
async def get_current_user(current_user: dict = Depends(AuthService.get_current_user)):
    """현재 로그인한 사용자 정보 조회"""
    return current_user

@router.post("/logout")
async def logout():
    """사용자 로그아웃"""
    # JWT 토큰은 클라이언트에서 삭제
    return {"message": "로그아웃되었습니다."} 