# src/calendar/router.py
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
import datetime as dt
import httpx

from .models import CalendarEvent, CreateEventRequest, GoogleAuthRequest, GoogleAuthResponse
from .service import GoogleCalendarService

from config.settings import settings
from src.auth.service import AuthService
from src.auth.repository import AuthRepository

router = APIRouter(prefix="/calendar", tags=["calendar"])

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# ---------------------------
# 내부 유틸: 액세스 토큰 보장 (만료 시 refresh)
# ---------------------------
async def _ensure_access_token(current_user: dict) -> str:
    """
    1) DB에서 access_token / refresh_token / expiry(있으면)를 읽는다
    2) 만료 임박(<=60초) 또는 만료면 refresh_token으로 새 access_token 발급
    3) 최신 access_token을 반환하고 DB에 반영
    """
    # 유저 레코드 조회 (이름이 다르면 find_user_by_id로 바꿔 사용)
    db_user = await AuthRepository.find_user_by_email(current_user["email"])
    if not db_user:
        raise HTTPException(status_code=401, detail="사용자 정보를 찾을 수 없습니다.")

    access_token = db_user.get("access_token")
    refresh_token = db_user.get("refresh_token")
    expiry = db_user.get("token_expiry") or db_user.get("expiry")  # 컬럼명에 맞게 조정

    # expiry 파싱
    def _to_dt(x):
        if not x:
            return None
        if isinstance(x, dt.datetime):
            return x
        try:
            return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        except Exception:
            return None

    expiry_dt = _to_dt(expiry)

    # 만료 임박 여부(60초 버퍼)
    needs_refresh = True
    if access_token and expiry_dt:
        needs_refresh = (expiry_dt - dt.datetime.utcnow()).total_seconds() < 60
    elif access_token and not expiry_dt:
        # 만료 정보를 저장하지 않는 구조라면 일단 사용해 보고 실패 시 상위에서 400 처리
        needs_refresh = False

    if not needs_refresh and access_token:
        return access_token

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Google 재로그인이 필요합니다 (refresh_token 없음).")

    # refresh 토큰으로 토큰 갱신
    async with httpx.AsyncClient(timeout=15) as client:
        data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        r = await client.post(GOOGLE_TOKEN_URL, data=data)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Google 토큰 갱신 실패: {r.text}")
        tok = r.json()

    new_access = tok["access_token"]
    new_expiry = (dt.datetime.utcnow() + dt.timedelta(seconds=tok.get("expires_in", 3600))).isoformat()

    # DB 업데이트 (컬럼명에 맞게 조정; expiry 저장 컬럼이 없다면 token_expiry 인자 제거 가능)
    await AuthRepository.update_google_user_info(
        email=current_user["email"],
        access_token=new_access,
        refresh_token=refresh_token,  # 보통 refresh는 응답에 다시 안 옴 -> 기존 값 유지
        profile_image=None,
        name=None,
        token_expiry=new_expiry  # 없는 시그니처면 이 인자 삭제
    )
    return new_access

# ---------------------------
# OAuth (선택 유지: 클라이언트에서 쓸 경우)
# ---------------------------
@router.get("/auth-url")
async def get_google_auth_url():
    """
    Google OAuth 인증 URL 반환.
    캘린더 접근을 위해 scope에 https://www.googleapis.com/auth/calendar 포함 필요.
    """
    service = GoogleCalendarService()
    auth_url = service.get_authorization_url()  # 내부에서 scope에 calendar 포함되어야 함
    return {"auth_url": auth_url}

@router.post("/auth", response_model=GoogleAuthResponse)
async def authenticate_google(request: GoogleAuthRequest):
    """
    Google OAuth 코드로 액세스 토큰 발급.
    (백엔드에서 DB에 저장하는 흐름은 /auth/google/callback에서 이미 처리 중이라면,
     이 엔드포인트는 필요 없을 수 있습니다. 유지하되 클라이언트 테스트용으로 두세요.)
    """
    try:
        service = GoogleCalendarService()
        token_data = await service.get_access_token(request.code)
        return GoogleAuthResponse(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data["expires_in"],
            token_type=token_data["token_type"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google 인증 실패: {str(e)}")

# ---------------------------
# Events (쿼리 파라미터로 access_token 받기)
# ---------------------------
@router.get("/events")
async def get_calendar_events(
    access_token: str = Query(..., description="Google OAuth access token"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
    time_min: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-15T00:00:00+09:00"),
    time_max: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-16T00:00:00+09:00"),
):
    """
    Google OAuth access_token으로 Google Calendar 이벤트 조회
    """
    try:
        service = GoogleCalendarService()
        events = await service.get_calendar_events(
            access_token=access_token,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
        )
        return {"events": events}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 조회 실패: {str(e)}")

@router.post("/events", response_model=CalendarEvent)
async def create_calendar_event(
    event_data: CreateEventRequest,
    access_token: str = Query(..., description="Google OAuth access token"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """
    Google OAuth access_token으로 Google Calendar 이벤트 생성
    """
    try:
        service = GoogleCalendarService()
        event = await service.create_calendar_event(
            access_token=access_token,
            event_data=event_data,
            calendar_id=calendar_id,
        )
        return event
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 생성 실패: {str(e)}")

@router.delete("/events/{event_id}")
async def delete_calendar_event(
    event_id: str,
    access_token: str = Query(..., description="Google OAuth access token"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """
    Google OAuth access_token으로 Google Calendar 이벤트 삭제
    """
    try:
        service = GoogleCalendarService()
        success = await service.delete_calendar_event(
            access_token=access_token,
            event_id=event_id,
            calendar_id=calendar_id,
        )
        if success:
            return {"message": "이벤트가 성공적으로 삭제되었습니다."}
        raise HTTPException(status_code=400, detail="이벤트 삭제에 실패했습니다.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 삭제 실패: {str(e)}")

@router.get("/test")
async def test_calendar_api():
    return {
        "message": "Google Calendar API 연결 준비 완료",
        "status": "ready",
        "available_endpoints": [
            "GET /calendar/auth-url",
            "POST /calendar/auth",
            "GET /calendar/events",
            "POST /calendar/events",
            "DELETE /calendar/events/{event_id}",
        ],
    }
