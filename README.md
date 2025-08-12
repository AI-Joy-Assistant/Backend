# AI Joy Assistant Backend

AI Joy Assistant의 백엔드 API 서버입니다.

## 주요 변경사항

### JWT 인증으로 통일 (2024년 12월)

- **기존**: 세션 기반 인증과 JWT 인증이 혼용되어 사용됨
- **변경**: 모든 인증을 JWT 기반으로 통일
- **제거된 기능**: 
  - 세션 미들웨어 (`SessionMiddleware`)
  - 세션 기반 토큰 엔드포인트 (`/auth/token`)
  - 세션에 사용자 정보 저장
- **추가된 기능**:
  - JWT 토큰 갱신 엔드포인트 (`/auth/refresh`)
  - 공통 JWT 인증 미들웨어

## 기술 스택

- **Framework**: FastAPI (Python)
- **인증**: JWT (JSON Web Token)
- **데이터베이스**: Supabase (PostgreSQL)
- **OAuth**: Google OAuth2

## 환경 설정

### 필수 환경변수

`.env` 파일에 다음 변수들을 설정해야 합니다:

```env
# JWT 설정
JWT_SECRET=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=1

# Google OAuth 설정
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://localhost:3000/auth/google/callback

# Supabase 설정
SUPABASE_URL=your-supabase-url
SUPABASE_SERVICE_KEY=your-supabase-service-key
```

## 설치 및 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경변수 설정

`.env` 파일을 생성하고 위의 환경변수들을 설정합니다.

### 3. 서버 실행

```bash
python main.py
```

또는

```bash
uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

## API 엔드포인트

### 인증 (Authentication)

- `POST /auth/register` - 사용자 회원가입
- `POST /auth/login` - 사용자 로그인
- `GET /auth/google` - Google OAuth 인증 시작
- `GET /auth/google/callback` - Google OAuth 콜백 처리
- `POST /auth/refresh` - JWT 토큰 갱신
- `GET /auth/me` - 현재 사용자 정보 조회
- `PUT /auth/me` - 사용자 정보 수정
- `DELETE /auth/me` - 사용자 계정 삭제
- `POST /auth/logout` - 로그아웃

### 채팅 (Chat)

- `GET /chat/rooms` - 채팅방 목록 조회
- `GET /chat/messages/{other_user_id}` - 채팅 메시지 조회
- `POST /chat/send` - 메시지 전송
- `GET /chat/friends` - 친구 목록 조회

### 친구 (Friends)

- `GET /friends/requests` - 친구 요청 목록 조회
- `POST /friends/requests/{request_id}/accept` - 친구 요청 수락
- `POST /friends/requests/{request_id}/reject` - 친구 요청 거절
- `GET /friends/list` - 친구 목록 조회
- `DELETE /friends/{friend_id}` - 친구 삭제
- `POST /friends/add` - 친구 추가

### 캘린더 (Calendar)

- `GET /calendar/auth-url` - Google OAuth 인증 URL
- `POST /calendar/auth` - Google OAuth 인증
- `GET /calendar/events` - 캘린더 이벤트 조회
- `POST /calendar/events` - 캘린더 이벤트 생성
- `DELETE /calendar/events/{event_id}` - 캘린더 이벤트 삭제

## JWT 인증 사용법

### 1. 로그인 후 JWT 토큰 받기

```bash
# Google OAuth 로그인
GET /auth/google

# 또는 일반 로그인
POST /auth/login
{
  "email": "user@example.com",
  "password": "password"
}
```

### 2. API 요청 시 JWT 토큰 사용

```bash
GET /auth/me
Authorization: Bearer <your-jwt-token>
```

### 3. 토큰 갱신

```bash
POST /auth/refresh
{
  "refresh_token": "<your-refresh-token>"
}
```

## 개발 가이드

### 새로운 라우터 추가 시

1. JWT 인증이 필요한 경우 `get_current_user_id` 의존성 사용:

```python
from src.auth.router import get_current_user_id

@router.get("/example")
async def example_endpoint(current_user_id: str = Depends(get_current_user_id)):
    # 사용자 ID를 사용한 로직
    pass
```

2. JWT 인증이 필요 없는 경우 (예: 공개 API):

```python
@router.get("/public")
async def public_endpoint():
    # 공개 로직
    pass
```

## 문제 해결

### JWT 토큰 관련 오류

- **401 Unauthorized**: JWT 토큰이 없거나 만료됨
- **403 Forbidden**: JWT 토큰이 유효하지 않음

### Google OAuth 관련 오류

- **400 Bad Request**: OAuth 설정이 잘못됨
- **401 Unauthorized**: Google OAuth 인증 실패

## 라이센스

이 프로젝트는 MIT 라이센스 하에 배포됩니다. 