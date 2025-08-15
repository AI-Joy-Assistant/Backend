# src/calendar/router.py
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Optional
import datetime as dt
import httpx
import logging

from .models import CalendarEvent, CreateEventRequest, GoogleAuthRequest, GoogleAuthResponse
from .service import GoogleCalendarService

from config.settings import settings
from src.auth.service import AuthService
from src.auth.repository import AuthRepository

# 로깅 설정
logger = logging.getLogger(__name__)

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

    # 만료 임박 여부(60초 버퍼) - 타임존 일치시키기
    needs_refresh = False
    if access_token and expiry_dt:
        # expiry_dt가 타임존 정보가 있으므로 utcnow()도 타임존 정보를 추가
        now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        needs_refresh = (expiry_dt - now_utc).total_seconds() < 60
    elif access_token and not expiry_dt:
        # 만료 정보가 없으면 일단 사용해보고, 실패 시 갱신
        needs_refresh = False
    else:
        needs_refresh = True

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
    # 타임존 정보를 포함한 만료 시간 계산
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    new_expiry = (now_utc + dt.timedelta(seconds=tok.get("expires_in", 3600))).isoformat()

    # DB 업데이트
    await AuthRepository.update_google_user_info(
        email=current_user["email"],
        access_token=new_access,
        refresh_token=refresh_token,  # 보통 refresh는 응답에 다시 안 옴 -> 기존 값 유지
        profile_image=None,
        name=None
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
# Events (JWT 인증으로 자동 토큰 갱신)
# ---------------------------
@router.get("/events")
async def get_calendar_events(
    current_user: dict = Depends(AuthService.get_current_user),
    calendar_id: str = Query("primary", description="캘린더 ID"),
    time_min: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-15T00:00:00+09:00"),
    time_max: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-16T00:00:00+09:00"),
):
    """
    앱 JWT로 인증 후 Google Calendar 이벤트 조회 (자동 토큰 갱신)
    """
    try:
        # Google 액세스 토큰 보장 (만료 시 자동 갱신)
        google_access_token = await _ensure_access_token(current_user)
        
        service = GoogleCalendarService()
        events = await service.get_calendar_events(
            access_token=google_access_token,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
        )
        return {"events": events}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 조회 실패: {str(e)}")

# ---------------------------
# Events (기존 방식 - 쿼리 파라미터로 access_token 받기)
# ---------------------------
@router.get("/events/legacy")
async def get_calendar_events_legacy(
    access_token: str = Query(..., description="Google OAuth access token"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
    time_min: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-15T00:00:00+09:00"),
    time_max: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-16T00:00:00+09:00"),
):
    """
    Google OAuth access_token으로 Google Calendar 이벤트 조회 (기존 방식)
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
    current_user: dict = Depends(AuthService.get_current_user),
    calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """
    앱 JWT로 인증 후 Google Calendar 이벤트 생성 (자동 토큰 갱신)
    """
    try:
        # Google 액세스 토큰 보장 (만료 시 자동 갱신)
        google_access_token = await _ensure_access_token(current_user)
        
        service = GoogleCalendarService()
        event = await service.create_calendar_event(
            access_token=google_access_token,
            event_data=event_data,
            calendar_id=calendar_id,
        )
        return event
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 생성 실패: {str(e)}")

@router.delete("/events/{event_id}")
async def delete_calendar_event(
    event_id: str,
    current_user: dict = Depends(AuthService.get_current_user),
    calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """
    앱 JWT로 인증 후 Google Calendar 이벤트 삭제 (자동 토큰 갱신)
    """
    try:
        # Google 액세스 토큰 보장 (만료 시 자동 갱신)
        google_access_token = await _ensure_access_token(current_user)
        
        service = GoogleCalendarService()
        success = await service.delete_calendar_event(
            access_token=google_access_token,
            event_id=event_id,
            calendar_id=calendar_id,
        )
        if success:
            return {"message": "이벤트가 성공적으로 삭제되었습니다."}
        raise HTTPException(status_code=400, detail="이벤트 삭제에 실패했습니다.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 삭제 실패: {str(e)}")

# ---------------------------
# Google Calendar Webhook (실시간 동기화)
# ---------------------------
@router.post("/webhook")
async def google_calendar_webhook(request: Request):
    """
    Google Calendar 웹훅 처리
    - 캘린더 변경사항을 실시간으로 감지
    - 클라이언트에게 실시간 알림 전송
    """
    try:
        # 웹훅 데이터 파싱
        webhook_data = await request.json()
        
        # Google Calendar 웹훅 검증
        if "state" in webhook_data:
            # 구독 확인 요청
            return {"status": "ok", "challenge": webhook_data.get("state")}
        
        # 실제 캘린더 변경사항 처리
        if "events" in webhook_data:
            events = webhook_data["events"]
            logger.info(f"[WEBHOOK] 캘린더 변경 감지: {len(events)}개 이벤트")
            
            # 여기서 클라이언트에게 실시간 알림을 보낼 수 있음
            # 예: WebSocket, Server-Sent Events, 또는 푸시 알림
            
            return {"status": "success", "processed_events": len(events)}
        
        return {"status": "received", "data": webhook_data}
        
    except Exception as e:
        logger.error(f"[WEBHOOK] 웹훅 처리 오류: {str(e)}")
        raise HTTPException(status_code=400, detail=f"웹훅 처리 실패: {str(e)}")

@router.post("/subscribe")
async def subscribe_to_calendar_webhook(
    current_user: dict = Depends(AuthService.get_current_user),
    calendar_id: str = Query("primary", description="캘린더 ID")
):
    """
    Google Calendar 웹훅 구독 설정
    """
    try:
        # Google 액세스 토큰 보장
        google_access_token = await _ensure_access_token(current_user)
        
        # Google Calendar API로 웹훅 구독 요청
        webhook_url = f"{settings.BASE_URL}/calendar/webhook"
        
        subscription_data = {
            "id": f"webhook_{current_user['email']}_{calendar_id}",
            "type": "web_hook",
            "address": webhook_url,
            "params": {
                "ttl": "2592000"  # 30일
            }
        }
        
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch"
        headers = {
            "Authorization": f"Bearer {google_access_token}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=subscription_data, headers=headers)
            response.raise_for_status()
            
        result = response.json()
        logger.info(f"[WEBHOOK] 구독 성공: {result.get('id')}")
        
        return {
            "status": "success",
            "subscription_id": result.get("id"),
            "expiration": result.get("expiration")
        }
        
    except Exception as e:
        logger.error(f"[WEBHOOK] 구독 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"웹훅 구독 실패: {str(e)}")

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
