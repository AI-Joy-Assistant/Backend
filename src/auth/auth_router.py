from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import Optional
import json
import datetime as dt
from urllib.parse import urlencode
import jwt
from .auth_models import UserCreate, UserLogin, UserResponse, TokenResponse
from .auth_service import AuthService
from .auth_repository import AuthRepository
from config.database import get_supabase_client  # (사용 안 해도 유지)
from config.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate):
    """사용자 회원가입"""
    try:
        user = await AuthService.register_user(user_data)
        return user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

from .auth_models import UserRegisterRequest
@router.post("/register/google", response_model=TokenResponse)
async def register_google(data: UserRegisterRequest):
    """Google 회원가입 완료 및 토큰 발급"""
    try:
        # 1. register_token 검증
        payload = jwt.decode(data.register_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        
        # 2. 사용자 생성
        google_user_data = {
            "email": payload["email"],
            "name": data.name,
            "handle": data.handle,
            "profile_image": payload.get("picture"),
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "status": True,
            "token_expiry": payload.get("token_expiry"),
            "google_id": payload.get("google_id")
        }
        
        # create_google_user가 handle을 지원하도록 수정되었으므로 그대로 전달
        user = await AuthRepository.create_google_user(google_user_data)
        
        # 3. 로그인 처리 (JWT 발급)
        # AuthService.login_google_user는 email로 조회하므로 바로 호출 가능
        token = await AuthService.login_google_user({"email": payload["email"]})
        
        return token
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="가입 토큰이 만료되었습니다.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 가입 토큰입니다.")
    except Exception as e:
        print(f"❌ Google 회원가입 실패: {str(e)}")
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
async def google_auth(request: Request, redirect_scheme: Optional[str] = None):
    """
    Google OAuth 인증 시작
    - redirect_scheme: 프론트엔드 리다이렉트 스킴 (예: exp://..., frontend://...)
    """
    scopes = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/calendar",
    ]
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        # state 파라미터에 redirect_scheme 저장 (JSON)
        "state": json.dumps({"redirect_scheme": redirect_scheme}) if redirect_scheme else ""
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
async def google_auth_callback(code: str, request: Request, state: Optional[str] = None):
    """Google OAuth 콜백 처리"""
    try:
        import httpx

        # state에서 redirect_scheme 추출
        redirect_scheme = "frontend://auth-success" # 기본값
        if state:
            try:
                state_data = json.loads(state)
                if state_data.get("redirect_scheme"):
                    redirect_scheme = state_data.get("redirect_scheme")
                    # auth-success가 포함되어 있다면 제거 (뒤에서 붙임) -> 아니, 그냥 통째로 받는게 나음
                    # 하지만 Linking.createURL('auth-success')는 전체 URL을 반환함.
                    # 따라서 redirect_scheme 변수명보다는 target_url이 더 적절하지만, 
                    # 기존 로직과의 호환성을 위해 파싱 로직 추가.
                    
                    # 만약 redirect_scheme이 'exp://...' 형태라면 쿼리 파라미터를 붙여야 함.
                    # Linking.createURL('auth-success') -> 'exp://.../--/auth-success'
            except:
                pass
        
        print(f"🎯 Target Redirect URI: {redirect_scheme}")

        print("🔍 Google OAuth 콜백 시작...")
        # ... (중략) ...

        # 1) 액세스 토큰 교환
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        }

        print("🔄 Google 액세스 토큰 교환 중...")
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(token_url, data=token_data)
            token_response.raise_for_status()
            tokens = token_response.json()
            print("✅ Google 액세스 토큰 교환 성공")

        # 만료 시각 계산
        expires_in = tokens.get("expires_in", 3600)
        token_expiry = (dt.datetime.utcnow() + dt.timedelta(seconds=expires_in)).isoformat()

        # 2) 사용자 정보 가져오기
        user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        print("🔄 Google 사용자 정보 가져오는 중...")
        async with httpx.AsyncClient(timeout=15) as client:
            user_response = await client.get(user_info_url, headers=headers)
            user_response.raise_for_status()
            user_info = user_response.json()
            print(f"✅ Google 사용자 정보: {user_info.get('email')}, {user_info.get('name')}")

        # 3) 기존 사용자 확인
        try:
            print("🔍 기존 사용자 확인 중...")
            token = await AuthService.login_google_user(user_info)
            print("✅ 기존 사용자 로그인 성공")

            # 기존 사용자는 토큰/프로필만 업데이트
            print("🔄 기존 사용자 정보 업데이트 중...")
            profile_image = user_info.get("picture")
            
            try:
                await AuthRepository.update_google_user_info(
                    email=user_info["email"],
                    access_token=tokens.get("access_token"),
                    refresh_token=tokens.get("refresh_token"),
                    profile_image=profile_image,
                    token_expiry=token_expiry,
                )
            except TypeError:
                await AuthRepository.update_google_user_info(
                    email=user_info["email"],
                    access_token=tokens.get("access_token"),
                    refresh_token=tokens.get("refresh_token"),
                    profile_image=profile_image,
                )

            # 세션 저장 (앱 JWT)
            request.session["user"] = {
                "id": user_info["id"],
                "email": user_info["email"],
                "name": user_info.get("name", ""),
                "access_token": token.access_token,
            }

            # 5) 리다이렉트 처리
            # 웹 환경 감지: redirect_scheme이 http://localhost로 시작하면 웹
            is_web = redirect_scheme and redirect_scheme.startswith("http://localhost")
            
            if is_web:
                # 웹 환경: HTMLResponse로 postMessage 사용
                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>로그인 성공</title>
                </head>
                <body>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'GOOGLE_LOGIN_SUCCESS',
                                token: '{token.access_token}'
                            }}, '*');
                            window.close();
                        }} else {{
                            window.location.href = '/';
                        }}
                    </script>
                    <h1>로그인 성공!</h1>
                    <p>창이 자동으로 닫힙니다...</p>
                </body>
                </html>
                """
                print(f"🌐 웹 환경 감지: HTMLResponse 반환")
                return HTMLResponse(content=html_content)
            elif redirect_scheme:
                # 모바일 환경: RedirectResponse 사용
                separator = "&" if "?" in redirect_scheme else "?"
                final_redirect_url = f"{redirect_scheme}{separator}token={token.access_token}"
                print(f"📱 모바일 리다이렉트: {final_redirect_url}")
                return RedirectResponse(url=final_redirect_url)
            
            # redirect_scheme이 없는 경우 (예외 상황)
            if request.headers.get("user-agent", "").lower().find("mobile") == -1:
                 # 데스크탑/웹 환경
                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>로그인 성공</title>
                </head>
                <body>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'GOOGLE_LOGIN_SUCCESS',
                                token: '{token.access_token}',
                                user: {json.dumps(user_info)}
                            }}, '*');
                            window.close();
                        }} else {{
                            window.location.href = '/';
                        }}
                    </script>
                    <h1>로그인 성공!</h1>
                    <p>창이 자동으로 닫힙니다...</p>
                </body>
                </html>
                """
                return HTMLResponse(content=html_content)
            else:
                # 모바일이지만 redirect_scheme이 없는 경우 (예외 상황)
                return RedirectResponse(url=f"frontend://auth-success?token={token.access_token}")

        except Exception:
            # (b) 신규 사용자 -> 회원가입 페이지로 리다이렉트
            print("🆕 신규 사용자 감지 -> 회원가입 페이지로 이동")
            
            # 임시 등록 토큰 생성
            register_payload = {
                "email": user_info["email"],
                "google_id": user_info.get("id"),
                "picture": user_info.get("picture"),
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "token_expiry": token_expiry,
                "exp": dt.datetime.utcnow() + dt.timedelta(minutes=30)
            }
            register_token = jwt.encode(register_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
            
            # 쿼리 파라미터 인코딩
            params = {
                "register_token": register_token,
                "email": user_info["email"],
                "name": user_info.get("name", ""),
                "picture": user_info.get("picture", "")
            }
            query_string = urlencode(params)
            
            # 웹 환경 감지
            is_web = redirect_scheme and redirect_scheme.startswith("http://localhost")
            
            if is_web:
                # 웹 환경: HTMLResponse로 postMessage 사용
                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>회원가입 필요</title>
                </head>
                <body>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'GOOGLE_REGISTER_REQUIRED',
                                register_token: '{register_token}',
                                email: '{user_info["email"]}',
                                name: '{user_info.get("name", "")}',
                                picture: '{user_info.get("picture", "")}'
                            }}, '*');
                            window.close();
                        }} else {{
                            window.location.href = '/';
                        }}
                    </script>
                    <h1>회원가입이 필요합니다!</h1>
                    <p>창이 자동으로 닫힙니다...</p>
                </body>
                </html>
                """
                print(f"🌐 웹 환경 신규 회원가입: HTMLResponse 반환")
                return HTMLResponse(content=html_content)
            else:
                # 모바일 환경: RedirectResponse 사용
                separator = "&" if "?" in redirect_scheme else "?"
                final_redirect_url = f"{redirect_scheme}{separator}auth_action=register&{query_string}"
                print(f"📱 모바일 신규 회원가입 리다이렉트: {final_redirect_url}")
                return RedirectResponse(url=final_redirect_url)

    except Exception as e:
        print(f"❌ Google OAuth 콜백 오류: {str(e)}")
        # 에러 시에도 RedirectResponse 시도
        return RedirectResponse(url=f"frontend://auth-error?error={str(e)}")

@router.get("/token")
async def get_token(request: Request):
    """세션에서 앱 토큰(JWT) 가져오기"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return {"accessToken": user.get("access_token")}

@router.get("/google-token")
async def get_google_token(request: Request):
    """세션에서 Google OAuth access_token 가져오기"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    
    # 데이터베이스에서 Google OAuth access_token 가져오기
    try:
        from .auth_repository import AuthRepository
        user_data = await AuthRepository.find_user_by_email(user.get("email"))
        if user_data and user_data.get("access_token"):
            return {"access_token": user_data.get("access_token")}
        else:
            raise HTTPException(status_code=404, detail="Google OAuth 토큰을 찾을 수 없습니다.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"토큰 조회 실패: {str(e)}")

@router.get("/me", response_model=UserResponse)
async def get_current_user(current_user: dict = Depends(AuthService.get_current_user)):
    """현재 로그인한 사용자 정보 조회"""
    return current_user

@router.post("/logout")
async def logout(request: Request):
    """사용자 로그아웃"""
    if "user" in request.session:
        del request.session["user"]
    return {"message": "로그아웃되었습니다."}

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
        if "user" in request.session:
            del request.session["user"]
        return {"message": "계정이 성공적으로 삭제되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/profile-image/{user_id}")
async def get_profile_image(user_id: str):
    """사용자 프로필 이미지 프록시"""
    try:
        user = await AuthRepository.find_user_by_id(user_id)
        if not user or not user.get('profile_image'):
            raise HTTPException(status_code=404, detail="프로필 이미지를 찾을 수 없습니다.")

        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(user['profile_image'])
            response.raise_for_status()

            from fastapi.responses import Response
            return Response(
                content=response.content,
                media_type=response.headers.get('content-type', 'image/png'),
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*'
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이미지 로드 실패: {str(e)}")

@router.post("/refresh")
async def refresh_access_token(request: Request):
    """
    만료된 앱 JWT를 새로 발급.
    - Authorization: Bearer <expired_jwt> 를 보내면,
      payload(email)만 읽어 DB의 refresh_token으로 Google 재발급 -> 새 앱 JWT 반환
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization 헤더가 없습니다.")

    expired_token = auth_header.split(" ")[1]

    try:
        # ▲ 변경: 만료 무시하고 payload 추출
        payload = jwt.decode(
            expired_token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False}  # ▲ 변경
        )
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="토큰에 이메일이 없습니다.")

        # DB에서 사용자/리프레시 토큰 조회
        user = await AuthRepository.find_user_by_email(email)
        if not user or not user.get("refresh_token"):
            raise HTTPException(status_code=401, detail="리프레시 토큰이 없습니다.")

        # 구글에서 새 access_token 받으면서 앱 JWT 재발급
        result = await AuthService.get_new_access_token_from_google(user["refresh_token"])
        if result["status"] != 200:
            raise HTTPException(status_code=result["status"], detail=result["body"])
        return result["body"]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"토큰 재발급 실패: {str(e)}")