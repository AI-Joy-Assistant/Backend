from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
import uuid

# 사용자 모델
class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None

class UserCreate(UserBase):
    password: Optional[str] = None
    google_id: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
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
    created_at: Optional[datetime] = None

class UserProfileResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: Optional[str] = None
    profile_image: Optional[str] = None
    status: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class MessageResponse(BaseModel):
    message: str 