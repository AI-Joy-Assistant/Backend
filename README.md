# AI Joy Assistant Backend API

FastAPI 기반의 백엔드 API 서버입니다.

## 🚀 빠른 시작

### 1. 의존성 설치
```bash
pip install -r requirements.txt
```

### 2. 환경변수 설정
```bash
# .env 파일 생성
cp env.example .env
```

`.env` 파일을 편집하여 필요한 설정값들을 입력하세요.

### 3. 서버 실행
```bash
python main.py
```

서버가 `http://localhost:8000`에서 실행됩니다.

## 📚 API 문서

- Swagger UI: `http://localhost:8000/api-docs`
- ReDoc: `http://localhost:8000/redoc`

## 🔧 Google Calendar API 연동

### 1. Google Cloud Console 설정

1. [Google Cloud Console](https://console.cloud.google.com/)에 접속
2. 새 프로젝트 생성 또는 기존 프로젝트 선택
3. **API 및 서비스** > **사용자 인증 정보**로 이동
4. **사용자 인증 정보 만들기** > **OAuth 2.0 클라이언트 ID** 선택
5. 애플리케이션 유형: **웹 애플리케이션** 선택
6. 승인된 리디렉션 URI에 `http://localhost:8000/auth/google/callback` 추가
7. **Google Calendar API** 활성화

### 2. 환경변수 설정

`.env` 파일에 Google OAuth 정보를 추가:

```env
GOOGLE_CLIENT_ID=your-google-client-id-here
GOOGLE_CLIENT_SECRET=your-google-client-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
```

### 3. API 엔드포인트

#### 캘린더 이벤트 조회
```http
GET /calendar/events
```

**파라미터:**
- `access_token` (필수): Google 액세스 토큰
- `calendar_id` (선택): 캘린더 ID (기본값: "primary")
- `time_min` (선택): 시작 시간
- `time_max` (선택): 종료 시간

#### Google OAuth 인증 URL 생성
```http
GET /calendar/auth-url
```

#### Google OAuth 인증 처리
```http
POST /calendar/auth
Content-Type: application/json

{
  "code": "authorization_code_from_google",
  "redirect_uri": "http://localhost:8000/auth/google/callback"
}
```

#### 캘린더 이벤트 생성
```http
POST /calendar/events?access_token=your_access_token
Content-Type: application/json

{
  "summary": "일정 제목",
  "description": "일정 설명",
  "start_time": "2025-08-06T19:00:00+09:00",
  "end_time": "2025-08-06T20:00:00+09:00",
  "location": "장소",
  "attendees": ["user@example.com"]
}
```

#### 캘린더 이벤트 삭제
```http
DELETE /calendar/events/{event_id}?access_token=your_access_token
```

### 4. 테스트

API가 정상적으로 작동하는지 확인:

```http
GET /calendar/test
```

## 🔐 인증

현재 Google OAuth 2.0을 통한 인증을 지원합니다.

### 인증 플로우

1. **인증 URL 요청**: `/calendar/auth-url`
2. **사용자 리디렉션**: Google 로그인 페이지로 이동
3. **인증 코드 수신**: Google에서 인증 코드 반환
4. **액세스 토큰 교환**: `/calendar/auth`로 인증 코드를 액세스 토큰으로 교환
5. **API 호출**: 액세스 토큰으로 Google Calendar API 호출

## 📁 프로젝트 구조

```
backend/
├── main.py                 # FastAPI 애플리케이션 진입점
├── requirements.txt        # Python 의존성
├── env.example            # 환경변수 예시
├── config/
│   ├── settings.py        # 설정 관리
│   └── database.py        # 데이터베이스 설정
└── src/
    ├── auth/              # 인증 관련
    ├── calendar/          # Google Calendar API
    ├── chat/              # 채팅 기능
    └── friends/           # 친구 기능
```

## 🛠️ 개발

### 디버그 모드

```bash
python main.py
```

### 로그 확인

서버 로그에서 Google Calendar API 호출 상태를 확인할 수 있습니다:

- `🔍`: API 호출 시작
- `✅`: 성공
- `❌`: 오류

## 🚨 주의사항

1. **환경변수 보안**: `.env` 파일을 Git에 커밋하지 마세요
2. **Google OAuth**: 실제 배포 시 승인된 리디렉션 URI를 업데이트하세요
3. **토큰 관리**: 액세스 토큰은 일정 시간 후 만료되므로 리프레시 토큰을 사용하세요

## 📞 지원

문제가 발생하면 다음을 확인하세요:

1. 환경변수가 올바르게 설정되었는지
2. Google Cloud Console에서 API가 활성화되었는지
3. 리디렉션 URI가 올바른지
4. 서버 로그에서 오류 메시지 확인 