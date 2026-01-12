from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
import uuid

# 사용자 모델
class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    handle: Optional[str] = None  # handle 추가

class UserCreate(UserBase):
    password: Optional[str] = None
    google_id: Optional[str] = None
    terms_agreed: Optional[bool] = False  # 약관 동의 여부
    terms_agreed_at: Optional[datetime] = None  # 약관 동의 시각

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    handle: Optional[str] = None  # handle 추가
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    profile_image: Optional[str] = None
    status: Optional[bool] = None

class User(UserBase):
    id: uuid.UUID
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    profile_image: Optional[str] = None
    status: Optional[bool] = None
    terms_agreed: Optional[bool] = False  # 약관 동의 여부
    terms_agreed_at: Optional[datetime] = None  # 약관 동의 시각
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

# 인증 관련 응답 모델
class LoginResponse(BaseModel):
    message: str
    accessToken: str
    expiresIn: int
    user: dict

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    handle: Optional[str] = None  # handle 추가
    profile_image: Optional[str] = None
    created_at: Optional[datetime] = None
    access_token: Optional[str] = None  # Google 연동 여부 확인용 (값이 있으면 연동됨)

class UserProfileResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: Optional[str] = None
    handle: Optional[str] = None  # handle 추가
    profile_image: Optional[str] = None
    status: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class MessageResponse(BaseModel):
    message: str

class UserRegisterRequest(BaseModel):
    register_token: str
    name: str
    handle: str
    terms_agreed: bool = True  # 약관 동의 여부 (필수)

# Apple 로그인 요청 모델
class AppleAuthRequest(BaseModel):
    identity_token: str  # Apple이 제공하는 JWT 토큰
    authorization_code: str
    user_id: str  # Apple user identifier
    email: Optional[str] = None  # 첫 로그인 시에만 제공됨
    full_name: Optional[str] = None  # 첫 로그인 시에만 제공됨

class AppleRegisterRequest(BaseModel):
    register_token: str
    name: str
    handle: str
    terms_agreed: bool = True