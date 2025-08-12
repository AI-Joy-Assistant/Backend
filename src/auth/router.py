from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from typing import Optional
import json
from .models import UserCreate, UserLogin, UserResponse, TokenResponse
from .service import AuthService
from .repository import AuthRepository
from config.database import get_supabase_client

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate):
    """사용자 회원가입"""
    try:
        user = await AuthService.register_user(user_data)
        return user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login", response_model=TokenResponse)
async def login(user_data: UserLogin):
    """사용자 로그인"""
    try:
        token = await AuthService.login_user(user_data)
        return token
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.get("/google")
async def google_auth():
    """Google OAuth 인증 시작"""
    from config.settings import settings
    
    # Google OAuth URL 생성 (리프레시 토큰을 받기 위해 prompt=consent 추가)
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={settings.GOOGLE_CLIENT_ID}&"
        f"redirect_uri={settings.GOOGLE_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=openid%20email%20profile&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    
    return RedirectResponse(url=auth_url)

@router.get("/google/callback")
async def google_auth_callback(code: str, request: Request):
    """Google OAuth 콜백 처리"""
    try:
        from config.settings import settings
        import httpx
        
        print("🔍 Google OAuth 콜백 시작...")
        print(f"📝 받은 코드: {code[:20]}...")
        
        # 액세스 토큰 교환
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        }
        
        print("🔄 Google 액세스 토큰 교환 중...")
        async with httpx.AsyncClient() as client:
            token_response = await client.post(token_url, data=token_data)
            token_response.raise_for_status()
            tokens = token_response.json()
            print("✅ Google 액세스 토큰 교환 성공")
            print(f"📊 받은 토큰 정보: access_token={bool(tokens.get('access_token'))}, refresh_token={bool(tokens.get('refresh_token'))}")
        
        # 사용자 정보 가져오기
        user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        print("🔄 Google 사용자 정보 가져오는 중...")
        async with httpx.AsyncClient() as client:
            user_response = await client.get(user_info_url, headers=headers)
            user_response.raise_for_status()
            user_info = user_response.json()
            print(f"✅ Google 사용자 정보: {user_info.get('email')}, {user_info.get('name')}")
        
        # Supabase에 사용자 정보 저장 또는 업데이트
        print("🔄 Supabase 사용자 처리 중...")
        
        # Google 사용자 정보로 회원가입/로그인 처리
        user_data = UserCreate(
            email=user_info["email"],
            password="",  # Google OAuth는 비밀번호가 없음
            name=user_info.get("name", "")
        )
        
        print(f"📝 처리할 사용자 데이터: {user_data.email}, {user_data.name}")
        
        try:
            # 기존 사용자인지 확인하고 로그인
            print("🔍 기존 사용자 확인 중...")
            token = await AuthService.login_google_user(user_info)
            print("✅ 기존 사용자 로그인 성공")
            
            # 기존 사용자의 경우 토큰과 프로필 이미지만 업데이트 (닉네임은 유지)
            print(f"🔄 기존 사용자 정보 업데이트 중...")
            print(f"📝 업데이트할 토큰: access_token={bool(tokens.get('access_token'))}, refresh_token={bool(tokens.get('refresh_token'))}")
            print(f"📸 프로필 이미지: {user_info.get('picture')}")
            
            # 프로필 이미지가 있으면 항상 업데이트
            profile_image = user_info.get("picture")
            if profile_image:
                print(f"✅ 프로필 이미지 업데이트: {profile_image}")
            
            await AuthRepository.update_google_user_info(
                email=user_info["email"],
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token"),
                profile_image=profile_image,
                name=None  # 기존 사용자의 경우 닉네임은 변경하지 않음
            )
            print("✅ 기존 사용자 정보 업데이트 완료")
            
        except Exception as e:
            print(f"⚠️ 기존 사용자 로그인 실패: {str(e)}")
            # 새 사용자라면 회원가입
            print("🆕 새 사용자 회원가입 중...")
            try:
                # Google 사용자 정보를 포함하여 새 사용자 생성
                google_user_data = {
                    "email": user_info["email"],
                    "name": user_info.get("name", ""),
                    "profile_image": user_info.get("picture"),
                    "access_token": tokens.get("access_token"),
                    "refresh_token": tokens.get("refresh_token"),
                    "status": True
                }
                
                print(f"📝 새 사용자 생성 데이터: {google_user_data}")
                print(f"📊 토큰 정보: access_token={bool(google_user_data.get('access_token'))}, refresh_token={bool(google_user_data.get('refresh_token'))}")
                
                user = await AuthRepository.create_google_user(google_user_data)
                print("✅ 새 사용자 회원가입 성공")
                token = await AuthService.login_google_user(user_info)
                print("✅ 새 사용자 로그인 성공")
            except Exception as register_error:
                print(f"❌ 새 사용자 회원가입 실패: {str(register_error)}")
                raise register_error
        
        # HTML 응답으로 브라우저 창 닫기 및 JWT 토큰 전달
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>로그인 성공</title>
        </head>
        <body>
            <script>
                // 부모 창에 JWT 토큰 전달
                if (window.opener) {{
                    window.opener.postMessage({{
                        type: 'GOOGLE_LOGIN_SUCCESS',
                        token: '{token.access_token}',
                        user: {json.dumps(user_info)}
                    }}, '*');
                    window.close();
                }} else {{
                    // 새 창에서 열린 경우 리다이렉트
                    window.location.href = 'http://localhost:8081?token={token.access_token}';
                }}
            </script>
            <h1>로그인 성공!</h1>
            <p>창이 자동으로 닫힙니다...</p>
        </body>
        </html>
        """
        
        print("✅ HTML 응답 생성 완료")
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        print(f"❌ Google OAuth 콜백 오류: {str(e)}")
        # 에러 발생 시 HTML 응답
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>로그인 실패</title>
        </head>
        <body>
            <script>
                if (window.opener) {{
                    window.opener.postMessage({{
                        type: 'GOOGLE_LOGIN_ERROR',
                        error: '{str(e)}'
                    }}, '*');
                    window.close();
                }} else {{
                    window.location.href = 'http://localhost:8081?error={str(e)}';
                }}
            </script>
            <h1>로그인 실패</h1>
            <p>오류: {str(e)}</p>
        </body>
        </html>
        """
        
        return HTMLResponse(content=html_content)

@router.get("/me", response_model=UserResponse)
async def get_current_user(current_user: dict = Depends(AuthService.get_current_user)):
    """현재 로그인한 사용자 정보 조회"""
    return current_user

@router.post("/logout")
async def logout(request: Request):
    """사용자 로그아웃"""
    try:
        # Authorization 헤더에서 JWT 토큰 추출
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            result = await AuthService.handle_logout(token)
            if result["status"] == 200:
                return {"message": "로그아웃되었습니다."}
            else:
                raise HTTPException(status_code=result["status"], detail=result["message"])
        else:
            return {"message": "로그아웃되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/refresh")
async def refresh_token(request: Request):
    """JWT 토큰 갱신"""
    try:
        from .models import RefreshTokenRequest
        
        # 요청 본문에서 리프레시 토큰 추출
        body = await request.json()
        refresh_token = body.get("refresh_token")
        
        if not refresh_token:
            raise HTTPException(status_code=400, detail="리프레시 토큰이 필요합니다.")
        
        # 리프레시 토큰으로 새 액세스 토큰 발급
        result = await AuthService.get_new_access_token_from_google(refresh_token)
        
        if result["status"] == 200:
            return result["body"]
        else:
            raise HTTPException(status_code=result["status"], detail=result["body"]["message"])
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/me")
async def update_user_info(
    request: Request,
    user_data: dict,
    current_user: dict = Depends(AuthService.get_current_user)
):
    """사용자 정보 수정"""
    try:
        updated_user = await AuthService.update_user_info(current_user["id"], user_data)
        return updated_user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/me")
async def delete_user(
    request: Request,
    current_user: dict = Depends(AuthService.get_current_user)
):
    """사용자 계정 삭제"""
    try:
        await AuthService.delete_user(current_user["id"])
        return {"message": "계정이 성공적으로 삭제되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) 