import httpx
from datetime import datetime, timedelta
from urllib.parse import urlencode
from typing import Dict, Any, Optional, Tuple
import jwt
from fastapi import Request
from config.settings import settings
from .repository import AuthRepository
from .models import LoginResponse, TokenResponse, UserProfileResponse, UserCreate, UserLogin, UserResponse

class AuthService:
    
    @staticmethod
    def create_jwt_access_token(user: Dict[str, Any]) -> str:
        """JWT 액세스 토큰 생성"""
        payload = {
            "id": user["id"],
            "email": user["email"],
            "exp": datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
        }
        return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def get_google_auth_url() -> str:
        """Google OAuth URL 생성"""
        params = {
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "access_type": "offline",
            "response_type": "code",
            "prompt": "consent",
            "scope": "openid email profile"
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    @staticmethod
    async def handle_google_callback(code: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """Google OAuth 콜백 처리"""
        try:
            print(f"🔐 실제 Google OAuth 처리 시작 (code: {code[:10]}...)")
            print(f"🔧 Client ID: {settings.GOOGLE_CLIENT_ID[:20]}...")
            print(f"🔧 Redirect URI: {settings.GOOGLE_REDIRECT_URI}")
            
            # Google OAuth 토큰 교환
            token_data = {
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code"
            }
            
            print(f"📤 Google에 토큰 요청 중...")
            
            async with httpx.AsyncClient() as client:
                # Access Token 받기
                token_response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data=token_data
                )
                
                print(f"📥 Google 토큰 응답 상태: {token_response.status_code}")
                if token_response.status_code != 200:
                    error_text = token_response.text
                    print(f"❌ Google 토큰 오류 응답: {error_text}")
                    raise Exception(f"Google 토큰 요청 실패 ({token_response.status_code}): {error_text}")
                
                token_response.raise_for_status()
                token_json = token_response.json()
                print(f"✅ Google 토큰 받기 성공")
                
                access_token = token_json.get("access_token")
                refresh_token = token_json.get("refresh_token")
                
                if not access_token:
                    print(f"❌ Access token이 응답에 없음: {token_json}")
                    raise Exception("Google OAuth access token 받기 실패")
                
                print(f"📤 Google 사용자 정보 요청 중...")
                # Google 사용자 정보 가져오기
                user_response = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                print(f"📥 Google 사용자 정보 응답 상태: {user_response.status_code}")
                user_response.raise_for_status()
                google_user = user_response.json()
                print(f"✅ Google 사용자 정보 받기 성공")
            
            # Google 사용자 정보 추출
            email = google_user.get("email")
            name = google_user.get("name")
            picture = google_user.get("picture")
            
            if not email:
                raise Exception("Google 계정에서 이메일을 가져올 수 없습니다")
            
            print(f"✅ Google 사용자 정보: {email}, {name}")
            
            # DB에서 사용자 찾기 또는 생성
            user = await AuthRepository.find_user_by_email(email)
            if not user:
                user_data = {
                    "email": email,
                    "name": name,
                    "profile_image": picture
                }
                user = await AuthRepository.create_user(user_data)
                print(f"🆕 새 사용자 생성: {email}")
            else:
                print(f"👤 기존 사용자 로그인: {email}")
            
            # 사용자 상태 업데이트
            await AuthRepository.update_user_status(email, True)
            
            # 리프레시 토큰 저장
            if refresh_token:
                await AuthRepository.update_refresh_token(user["id"], refresh_token)
            
            # JWT 액세스 토큰 발급
            try:
                print(f"🔍 사용자 데이터: {user}")
                jwt_access_token = AuthService.create_jwt_access_token(user)
                print(f"✅ JWT 토큰 생성 성공")
            except Exception as e:
                print(f"❌ JWT 토큰 생성 실패: {str(e)}")
                raise Exception(f"JWT 토큰 생성 오류: {str(e)}")
            
            response_data = {
                "message": f"환영합니다, {name}님!",
                "accessToken": jwt_access_token,
                "expiresIn": 3600,
                "user": {
                    "id": user["id"],  # DB 사용자 ID 추가
                    "email": email, 
                    "name": name, 
                    "picture": picture
                }
            }
            
            print(f"✅ 응답 데이터 생성 완료: {response_data}")
            return refresh_token, response_data
            
        except Exception as e:
            print(f"❌ Google OAuth 처리 오류: {str(e)}")
            print(f"🔍 오류 타입: {type(e)}")
            import traceback
            print(f"📍 스택 트레이스: {traceback.format_exc()}")
            
            # 실제 오류를 사용자에게 표시
            raise Exception(f"Google OAuth 설정 오류: {str(e)}")

    @staticmethod
    async def get_new_access_token_from_google(refresh_token: str) -> Dict[str, Any]:
        """Google에서 새 액세스 토큰 발급"""
        if not refresh_token:
            return {"status": 401, "body": {"message": "Refresh Token이 없습니다."}}
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token"
                    }
                )
                response.raise_for_status()
                token_data = response.json()
                google_access_token = token_data.get("access_token")
                
                # 사용자 정보 조회
                user_response = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {google_access_token}"}
                )
                user_response.raise_for_status()
                user_info = user_response.json()
                
                email = user_info.get("email")
                user = await AuthRepository.find_user_by_email(email)
                
                if not user:
                    return {"status": 404, "body": {"message": "해당 사용자를 찾을 수 없습니다."}}
                
                # JWT 액세스 토큰 발급
                jwt_access_token = AuthService.create_jwt_access_token(user)
                
                return {
                    "status": 200,
                    "body": {
                        "accessToken": jwt_access_token,
                        "expiresIn": 3600
                    }
                }
                
        except Exception as e:
            return {
                "status": 500,
                "body": {"message": f"accessToken 재발급 실패: {str(e)}"}
            }

    @staticmethod
    async def handle_logout(token: str) -> Dict[str, Any]:
        """로그아웃 처리"""
        if not token:
            return {"status": 401, "message": "Access Token이 없습니다."}
        
        try:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
            email = payload.get("email")
            
            user = await AuthRepository.find_user_by_email(email)
            if not user:
                return {"status": 404, "message": "해당 사용자를 찾을 수 없습니다."}
            
            await AuthRepository.update_user_status(email, False)
            await AuthRepository.clear_refresh_token(user["id"])
            
            return {"status": 200, "message": "로그아웃 완료"}
            
        except jwt.InvalidTokenError:
            return {"status": 401, "message": "유효하지 않은 Access Token입니다."}
        except Exception as e:
            return {"status": 500, "message": f"로그아웃 처리 오류: {str(e)}"}

    @staticmethod
    async def fetch_user_info_from_google(token: str) -> Dict[str, Any]:
        """JWT 토큰으로 사용자 정보 조회"""
        if not token:
            return {
                "status": 401,
                "body": {"message": "Access Token이 없습니다."}
            }
        
        try:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
            email = payload.get("email")
            
            user = await AuthRepository.find_user_by_email(email)
            if not user:
                return {
                    "status": 404,
                    "body": {"message": "사용자를 찾을 수 없습니다."}
                }
            
            return {
                "status": 200,
                "body": {
                    "id": user["id"],
                    "email": user["email"],
                    "name": user.get("name"),
                    "profile_image": user.get("profile_image"),
                    "status": user.get("status"),
                    "created_at": user.get("created_at"),
                    "updated_at": user.get("updated_at")
                }
            }
            
        except jwt.InvalidTokenError:
            return {
                "status": 401,
                "body": {"message": "유효하지 않은 Access Token입니다."}
            }
        except Exception as e:
            return {
                "status": 500,
                "body": {"message": f"사용자 정보 조회 오류: {str(e)}"}
            }

    @staticmethod
    async def register_user(user_data: UserCreate) -> UserResponse:
        """사용자 회원가입"""
        try:
            # 이메일 중복 확인
            existing_user = await AuthRepository.find_user_by_email(user_data.email)
            if existing_user:
                raise Exception("이미 존재하는 이메일입니다.")
            
            # 사용자 생성
            user = await AuthRepository.create_user({
                "email": user_data.email,
                "name": user_data.name,
                "password": user_data.password,  # 실제로는 해시화 필요
                "google_id": user_data.google_id
            })
            
            return UserResponse(
                id=user["id"],
                email=user["email"],
                name=user["name"],
                created_at=user["created_at"]
            )
        except Exception as e:
            raise Exception(f"회원가입 실패: {str(e)}")

    @staticmethod
    async def login_user(user_data: UserLogin) -> TokenResponse:
        """사용자 로그인"""
        try:
            # 사용자 확인
            user = await AuthRepository.find_user_by_email(user_data.email)
            if not user:
                raise Exception("존재하지 않는 사용자입니다.")
            
            # 비밀번호 확인 (실제로는 해시 비교 필요)
            if user.get("password") != user_data.password:
                raise Exception("비밀번호가 일치하지 않습니다.")
            
            # JWT 토큰 생성
            access_token = AuthService.create_jwt_access_token(user)
            
            return TokenResponse(
                access_token=access_token,
                token_type="bearer",
                expires_in=3600
            )
        except Exception as e:
            raise Exception(f"로그인 실패: {str(e)}")

    @staticmethod
    async def register_google_user(user_data: UserCreate) -> UserResponse:
        """Google OAuth 사용자 회원가입"""
        try:
            print(f"🔍 Google 회원가입 시작: {user_data.email}")
            
            # 이메일로 기존 사용자 확인
            print(f"🔍 이메일로 기존 사용자 확인: {user_data.email}")
            existing_user = await AuthRepository.find_user_by_email(user_data.email)
            if existing_user:
                print(f"✅ 기존 사용자 발견: {existing_user['email']}")
                return UserResponse(
                    id=existing_user["id"],
                    email=existing_user["email"],
                    name=existing_user["name"],
                    created_at=existing_user["created_at"]
                )
            
            # 새 사용자 생성
            print(f"🆕 새 Google 사용자 생성: {user_data.email}")
            user_data_dict = {
                "email": user_data.email,
                "name": user_data.name
            }
            print(f"📝 저장할 데이터: {user_data_dict}")
            
            user = await AuthRepository.create_google_user(user_data_dict)
            print(f"✅ 새 Google 사용자 생성 성공: {user['id']}")
            
            return UserResponse(
                id=user["id"],
                email=user["email"],
                name=user["name"],
                created_at=user["created_at"]
            )
        except Exception as e:
            print(f"❌ Google 회원가입 실패: {str(e)}")
            raise Exception(f"Google 회원가입 실패: {str(e)}")

    @staticmethod
    async def login_google_user(user_info: Dict[str, Any]) -> TokenResponse:
        """Google OAuth 사용자 로그인"""
        try:
            print(f"🔍 Google 로그인 시작: {user_info.get('email')}")
            
            # 이메일로 사용자 확인
            email = user_info["email"]
            print(f"🔍 이메일로 사용자 확인: {email}")
            user = await AuthRepository.find_user_by_email(email)
            if not user:
                print(f"❌ 이메일로 사용자를 찾을 수 없음: {email}")
                raise Exception("Google 계정으로 가입된 사용자가 아닙니다.")
            
            print(f"✅ Google 사용자 확인 성공: {user['email']}")
            
            # JWT 토큰 생성
            access_token = AuthService.create_jwt_access_token(user)
            print(f"✅ JWT 토큰 생성 성공")
            
            return TokenResponse(
                access_token=access_token,
                token_type="bearer",
                expires_in=3600
            )
        except Exception as e:
            print(f"❌ Google 로그인 실패: {str(e)}")
            raise Exception(f"Google 로그인 실패: {str(e)}")

    @staticmethod
    async def get_current_user(request: Request) -> Dict[str, Any]:
        """JWT 토큰으로 현재 사용자 정보 조회"""
        try:
            auth_header = request.headers.get("authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                raise Exception("Authorization 헤더가 없습니다.")
            
            token = auth_header.split(" ")[1]
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
            email = payload.get("email")
            
            user = await AuthRepository.find_user_by_email(email)
            if not user:
                raise Exception("사용자를 찾을 수 없습니다.")
            
            return user
        except jwt.InvalidTokenError:
            raise Exception("유효하지 않은 토큰입니다.")
        except Exception as e:
            raise Exception(f"사용자 정보 조회 실패: {str(e)}") 