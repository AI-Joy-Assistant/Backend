from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from typing import Optional
from .service import AuthService
from .models import LoginResponse, TokenResponse, UserProfileResponse, MessageResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

def get_auth_token(request: Request) -> Optional[str]:
    """Authorization 헤더에서 토큰 추출"""
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    return None

@router.get("/google", summary="구글 로그인 요청")
async def google_login():
    """Google OAuth 로그인 URL로 리디렉션"""
    url = AuthService.get_google_auth_url()
    return RedirectResponse(url=url)

@router.get("/google/callback", summary="구글 로그인 콜백")
async def google_callback(code: str, request: Request, response: Response):
    """구글 로그인 콜백 처리"""
    try:
        refresh_token, result = await AuthService.handle_google_callback(code)
        
        # 세션에 사용자 정보 저장 (id 포함)
        request.session["user"] = result["user"]
        print(f"💾 세션에 사용자 정보 저장: {result['user']['email']} (ID: {result['user']['id']})")
        
        # 리프레시 토큰을 쿠키에 저장
        if refresh_token:
            response.set_cookie(
                key="refreshToken",
                value=refresh_token,
                httponly=True,
                secure=False,  # 개발환경에서는 False, 프로덕션에서는 True
                samesite="strict",
                max_age=7 * 24 * 60 * 60  # 7일
            )
        
        # 성공 시 HTML 페이지 반환 (자동으로 창 닫기)
        success_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>로그인 성공</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    height: 100vh; 
                    margin: 0; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }}
                .container {{ 
                    text-align: center; 
                    padding: 40px;
                    background: rgba(255,255,255,0.1);
                    border-radius: 20px;
                    backdrop-filter: blur(10px);
                }}
                .success-icon {{ font-size: 60px; margin-bottom: 20px; }}
                .message {{ font-size: 24px; margin-bottom: 10px; }}
                .sub-message {{ font-size: 16px; opacity: 0.8; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="success-icon">🎉</div>
                <div class="message">로그인 성공!</div>
                <div class="sub-message">{result['message']}</div>
                <div class="sub-message">잠시 후 앱으로 돌아갑니다...</div>
            </div>
            <script>
                // 토큰을 부모 창으로 전달
                if (window.opener) {{
                    window.opener.postMessage({{
                        type: 'GOOGLE_LOGIN_SUCCESS',
                        accessToken: '{result['accessToken']}',
                        user: {result['user']},
                        message: '{result['message']}'
                    }}, '*');
                }}
                
                // 3초 후 창 닫기 시도
                setTimeout(() => {{
                    window.close();
                    // 창이 닫히지 않으면 사용자에게 안내
                    setTimeout(() => {{
                        document.body.innerHTML = `
                            <div class="container">
                                <div class="success-icon">✅</div>
                                <div class="message">로그인 완료</div>
                                <div class="sub-message">이 창을 닫고 앱으로 돌아가세요</div>
                            </div>
                        `;
                    }}, 1000);
                }}, 2000);
            </script>
        </body>
        </html>
        """
        
        return HTMLResponse(content=success_html, status_code=200)
        
    except Exception as e:
        # 에러 시에도 HTML 페이지 반환
        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>로그인 실패</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    height: 100vh; 
                    margin: 0; 
                    background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%);
                    color: white;
                }}
                .container {{ 
                    text-align: center; 
                    padding: 40px;
                    background: rgba(255,255,255,0.1);
                    border-radius: 20px;
                    backdrop-filter: blur(10px);
                }}
                .error-icon {{ font-size: 60px; margin-bottom: 20px; }}
                .message {{ font-size: 24px; margin-bottom: 10px; }}
                .sub-message {{ font-size: 16px; opacity: 0.8; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="error-icon">❌</div>
                <div class="message">로그인 실패</div>
                <div class="sub-message">다시 시도해 주세요</div>
            </div>
            <script>
                setTimeout(() => window.close(), 3000);
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=500)

@router.post("/token", summary="액세스 토큰 재발급", response_model=TokenResponse)
async def refresh_google_access_token(request: Request):
    """리프레시 토큰으로 액세스 토큰 재발급"""
    refresh_token = request.cookies.get("refreshToken")
    
    result = await AuthService.get_new_access_token_from_google(refresh_token)
    
    return JSONResponse(
        status_code=result["status"],
        content=result["body"]
    )

@router.get("/token")
async def get_auth_token(request: Request):
    """세션 기반으로 JWT 토큰 반환"""
    try:
        # 세션에서 사용자 정보 확인
        session_user = request.session.get("user")
        if not session_user:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        
        print(f"🔑 토큰 요청 - 세션 사용자: {session_user.get('email')}")
        
        # JWT 토큰 생성
        jwt_token = AuthService.create_jwt_access_token(session_user)
        
        response_data = {
            "accessToken": jwt_token,
            "expiresIn": 3600,
            "user": {
                "email": session_user.get("email"),
                "name": session_user.get("name"),
                "picture": session_user.get("picture")
            }
        }
        
        print(f"✅ JWT 토큰 발급 완료: {session_user.get('email')}")
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ 토큰 발급 오류: {str(e)}")
        raise HTTPException(status_code=500, detail="토큰 발급 중 오류가 발생했습니다")

@router.post("/logout", summary="로그아웃", response_model=MessageResponse)
async def logout(token: Optional[str] = Depends(get_auth_token)):
    """로그아웃 처리"""
    result = await AuthService.handle_logout(token)
    
    return JSONResponse(
        status_code=result["status"],
        content={"message": result["message"]}
    )

@router.get("/me", summary="내 정보 조회", response_model=UserProfileResponse)
async def get_google_profile(token: Optional[str] = Depends(get_auth_token)):
    """JWT 토큰으로 사용자 정보 조회"""
    result = await AuthService.fetch_user_info_from_google(token)
    
    return JSONResponse(
        status_code=result["status"],
        content=result["body"]
    ) 