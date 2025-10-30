from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from typing import Optional
import json
import datetime as dt
from urllib.parse import urlencode
import jwt
from .models import UserCreate, UserLogin, UserResponse, TokenResponse
from .service import AuthService
from .repository import AuthRepository
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
    """
    Google OAuth 인증 시작
    - 캘린더 접근을 위해 calendar scope 포함
    - refresh_token 확보를 위해 access_type=offline + prompt=consent 사용
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
        "scope": " ".join(scopes),            # 공백으로 합친 뒤 urlencode 처리
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
async def google_auth_callback(code: str, request: Request):
    """Google OAuth 콜백 처리"""
    try:
        import httpx

        print("🔍 Google OAuth 콜백 시작...")
        print(f"📝 받은 코드: {code[:20]}...")

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
            print(f"📊 받은 토큰 정보: access_token={bool(tokens.get('access_token'))}, refresh_token={bool(tokens.get('refresh_token'))}")

        # 만료 시각 계산(선택)
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

        # 3) Supabase에 사용자 저장/업데이트
        print("🔄 Supabase 사용자 처리 중...")

        user_data = UserCreate(
            email=user_info["email"],
            password="",  # Google OAuth는 비밀번호 없음
            name=user_info.get("name", "")
        )

        print(f"📝 처리할 사용자 데이터: {user_data.email}, {user_data.name}")

        try:
            # (a) 기존 사용자 로그인
            print("🔍 기존 사용자 확인 중...")
            token = await AuthService.login_google_user(user_info)
            print("✅ 기존 사용자 로그인 성공")

            # 기존 사용자는 토큰/프로필만 업데이트 (닉네임 유지)
            print("🔄 기존 사용자 정보 업데이트 중...")
            profile_image = user_info.get("picture")

            # update_google_user_info가 token_expiry를 받을 수도/안 받을 수도 있으므로 안전 처리
            try:
                await AuthRepository.update_google_user_info(
                    email=user_info["email"],
                    access_token=tokens.get("access_token"),
                    refresh_token=tokens.get("refresh_token"),
                    profile_image=profile_image,
                    name=None,  # 닉네임 변경 없음
                    token_expiry=token_expiry,
                )
            except TypeError:
                # 구버전 시그니처 호환
                await AuthRepository.update_google_user_info(
                    email=user_info["email"],
                    access_token=tokens.get("access_token"),
                    refresh_token=tokens.get("refresh_token"),
                    profile_image=profile_image,
                    name=None,
                )

            print("✅ 기존 사용자 정보 업데이트 완료")

        except Exception as e:
            # (b) 신규 사용자 회원가입
            print(f"⚠️ 기존 사용자 로그인 실패: {str(e)}")
            print("🆕 새 사용자 회원가입 중...")

            google_user_data = {
                "email": user_info["email"],
                "name": user_info.get("name", ""),
                "profile_image": user_info.get("picture"),
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "status": True,
                "token_expiry": token_expiry,
            }

            print(f"📝 새 사용자 생성 데이터: {google_user_data}")
            print(f"📊 토큰 정보: access_token={bool(google_user_data.get('access_token'))}, refresh_token={bool(google_user_data.get('refresh_token'))}")

            try:
                # create_google_user가 token_expiry를 안 받을 수도 있으므로 안전 처리
                try:
                    user = await AuthRepository.create_google_user(google_user_data)
                except TypeError:
                    google_user_data_fallback = {k: v for k, v in google_user_data.items() if k != "token_expiry"}
                    user = await AuthRepository.create_google_user(google_user_data_fallback)

                print("✅ 새 사용자 회원가입 성공")
                token = await AuthService.login_google_user(user_info)
                print("✅ 새 사용자 로그인 성공")
            except Exception as register_error:
                print(f"❌ 새 사용자 회원가입 실패: {str(register_error)}")
                raise register_error

        # 4) 세션에 앱 토큰 저장 (앱 JWT)
        print("💾 세션에 사용자 정보 저장 중...")
        request.session["user"] = {
            "id": user_info["id"],
            "email": user_info["email"],
            "name": user_info.get("name", ""),
            "access_token": token.access_token,  # 앱에서 쓰는 JWT
        }
        print("✅ 세션 저장 완료")

        # 5) HTML 응답으로 창 닫기 + 부모 창에 토큰 전달
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
                    // RN/Expo(모바일) 환경: 앱 스킴으로 리다이렉트하여 토큰 전달
                    window.location.href = 'frontend://auth-success?token={token.access_token}';
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
                    // RN/Expo(모바일) 환경: 앱 스킴으로 에러 전달
                    window.location.href = 'frontend://auth-error?error={str(e)}';
                }}
            </script>
            <h1>로그인 실패</h1>
            <p>오류: {str(e)}</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

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
        from .repository import AuthRepository
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
      payload(email)만 읽어 DB의 refresh_token으로 Google 재발급 → 새 앱 JWT 반환
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