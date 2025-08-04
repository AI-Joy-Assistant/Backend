# AI Joy Assistant Backend - Python FastAPI

AI Joy Assistant의 백엔드 API 서버입니다. Python FastAPI로 구현되었습니다.

## 기능

- Google OAuth 2.0 인증
- JWT 토큰 기반 인증
- Supabase 데이터베이스 연동
- 자동 API 문서화 (Swagger/OpenAPI)

## 요구사항

- Python 3.9 이상
- Supabase 계정 및 프로젝트

## 설치 및 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env` 파일을 생성하고 다음 내용을 추가하세요:

```env
# JWT 설정
JWT_SECRET=your_jwt_secret_key_here

# Google OAuth 설정
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=http://localhost:3000/auth/google/callback

# Supabase 설정
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_KEY=your_supabase_service_key

# 서버 설정 (선택사항)
PORT=3000
HOST=0.0.0.0
```

### 3. 서버 실행

```bash
# 개발 서버 실행 (자동 리로드)
python main.py

# 또는 uvicorn 직접 실행
uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

서버가 시작되면 다음 URL에서 확인할 수 있습니다:

- 서버: http://localhost:3000
- API 문서: http://localhost:3000/api-docs
- ReDoc: http://localhost:3000/redoc

## API 엔드포인트

### 인증 (Auth)

- `GET /auth/google` - Google 로그인 페이지로 리디렉션
- `GET /auth/google/callback` - Google OAuth 콜백 처리
- `POST /auth/token` - 액세스 토큰 재발급
- `POST /auth/logout` - 로그아웃
- `GET /auth/me` - 사용자 정보 조회

### 채팅 (Chat)

- `GET /chat/rooms` - 채팅방 목록 조회
- `GET /chat/messages/{other_user_id}` - 채팅 메시지 조회
- `POST /chat/send` - 메시지 전송
- `GET /chat/friends` - 친구 목록 조회
- `POST /chat/start-ai-session` - AI와 일정 조율 대화 시작
- `GET /chat/friend/{friend_id}` - 특정 친구와의 대화 내용 조회

### 친구 (Friends)

- `GET /friends/requests` - 친구 요청 목록 조회
- `POST /friends/requests/{request_id}/accept` - 친구 요청 수락
- `POST /friends/requests/{request_id}/reject` - 친구 요청 거절
- `GET /friends/list` - 친구 목록 조회
- `DELETE /friends/{friend_id}` - 친구 삭제
- `POST /friends/add` - 이메일로 친구 추가
- `GET /friends/search` - 사용자 검색

### 기타

- `GET /` - 루트 엔드포인트
- `GET /debug` - 디버그 정보

## 프로젝트 구조

```
Backend/
├── main.py                    # FastAPI 메인 애플리케이션
├── requirements.txt           # Python 의존성
├── config/
│   ├── __init__.py
│   ├── settings.py           # 환경 설정
│   └── database.py           # Supabase 클라이언트
└── src/
    ├── __init__.py
    ├── auth/
    │   ├── __init__.py
    │   ├── models.py         # Pydantic 모델
    │   ├── repository.py     # 데이터베이스 접근 계층
    │   ├── service.py        # 비즈니스 로직
    │   └── router.py         # FastAPI 라우터
    ├── chat/
    │   ├── __init__.py
    │   ├── models.py         # 채팅 관련 모델
    │   ├── repository.py     # 채팅 데이터베이스 접근
    │   ├── service.py        # 채팅 비즈니스 로직
    │   └── router.py         # 채팅 API 라우터
    └── friends/
        ├── __init__.py
        ├── models.py         # 친구 관련 모델
        ├── repository.py     # 친구 데이터베이스 접근
        ├── service.py        # 친구 비즈니스 로직
        └── router.py         # 친구 API 라우터
```

## 기술 스택

- **FastAPI** - 웹 프레임워크
- **Uvicorn** - ASGI 서버
- **Pydantic** - 데이터 검증 및 설정 관리
- **Supabase** - 데이터베이스
- **PyJWT** - JWT 토큰 처리
- **HTTPX** - HTTP 클라이언트 (Google API 호출)

## 개발 가이드

### 코드 스타일

- Python PEP 8 스타일 가이드 준수
- 타입 힌트 사용 권장
- Docstring으로 함수 설명 작성

### 새로운 API 추가

1. `src/` 하위에 새로운 모듈 디렉토리 생성
2. `models.py`, `repository.py`, `service.py`, `router.py` 파일 생성
3. `main.py`에 라우터 등록 