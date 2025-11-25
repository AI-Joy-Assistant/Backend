# src/calendar/router.py
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Optional
import datetime as dt
import httpx
import logging

from .calender_models import CalendarEvent, CreateEventRequest, GoogleAuthRequest, GoogleAuthResponse
from .calender_service import GoogleCalendarService

from config.settings import settings
from src.auth.auth_service import AuthService
from src.auth.auth_repository import AuthRepository

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
    db_user = await AuthRepository.find_user_by_email(current_user["email"])
    if not db_user:
        raise HTTPException(status_code=401, detail="사용자 정보를 찾을 수 없습니다.")

    access_token = db_user.get("access_token")
    refresh_token = db_user.get("refresh_token")
    expiry = db_user.get("token_expiry") or db_user.get("expiry")

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

    needs_refresh = False
    if access_token and expiry_dt:
        now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        needs_refresh = (expiry_dt - now_utc).total_seconds() < 60
    elif access_token and not expiry_dt:
        needs_refresh = False
    else:
        needs_refresh = True

    if not needs_refresh and access_token:
        return access_token

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Google 재로그인이 필요합니다 (refresh_token 없음).")

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
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    new_expiry = (now_utc + dt.timedelta(seconds=tok.get("expires_in", 3600))).isoformat()

    try:
        await AuthRepository.update_google_user_info(
            email=current_user["email"],
            access_token=new_access,
            refresh_token=refresh_token,
            profile_image=None,
            name=None,
            token_expiry=new_expiry,
        )
    except TypeError:
        await AuthRepository.update_google_user_info(
            email=current_user["email"],
            access_token=new_access,
            refresh_token=refresh_token,
            profile_image=None,
            name=None,
        )
    return new_access

# ---------------------------
# 내부 유틸: 다른 사용자 ID로 액세스 토큰 확보
# ---------------------------
async def _ensure_access_token_by_user_id(user_id: str) -> str:
    db_user = await AuthRepository.find_user_by_id(user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="대상 사용자를 찾을 수 없습니다.")

    access_token = db_user.get("access_token")
    refresh_token = db_user.get("refresh_token")
    expiry = db_user.get("token_expiry") or db_user.get("expiry")

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
    needs_refresh = False
    if access_token and expiry_dt:
        now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        needs_refresh = (expiry_dt - now_utc).total_seconds() < 60
    elif access_token and not expiry_dt:
        needs_refresh = False
    else:
        needs_refresh = True

    if not needs_refresh and access_token:
        return access_token

    if not refresh_token:
        raise HTTPException(status_code=401, detail="대상 사용자의 Google 재로그인이 필요합니다 (refresh_token 없음).")

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
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    new_expiry = (now_utc + dt.timedelta(seconds=tok.get("expires_in", 3600))).isoformat()

    # 이메일 대신 ID 기준 업데이트
    try:
        await AuthRepository.update_tokens(user_id=user_id, access_token=new_access)
    except Exception:
        pass

    return new_access

# ---------------------------
# OAuth (선택 유지)
# ---------------------------
@router.get("/auth-url")
async def get_google_auth_url():
    service = GoogleCalendarService()
    auth_url = service.get_authorization_url()
    return {"auth_url": auth_url}

@router.post("/auth", response_model=GoogleAuthResponse)
async def authenticate_google(request: GoogleAuthRequest):
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
    try:
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

@router.get("/events/legacy")
async def get_calendar_events_legacy(
        access_token: str = Query(..., description="Google OAuth access token"),
        calendar_id: str = Query("primary", description="캘린더 ID"),
        time_min: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-15T00:00:00+09:00"),
        time_max: Optional[str] = Query(None, description="ISO8601 ex) 2025-08-16T00:00:00+09:00"),
):
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
    try:
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
    try:
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
# 공통 가용 시간 계산
# ---------------------------
@router.get("/common-free")
async def get_common_free_slots(
        friend_id: str,
        duration_minutes: int = Query(60, ge=15, le=240),
        time_min: Optional[str] = Query(None, description="ISO8601"),
        time_max: Optional[str] = Query(None, description="ISO8601"),
        current_user: dict = Depends(AuthService.get_current_user),
):
    try:
        # 각 사용자 액세스 토큰 확보 (만료 시 리프레시)
        me_access = await _ensure_access_token(current_user)
        friend_access = await _ensure_access_token_by_user_id(friend_id)

        service = GoogleCalendarService()
        # 이벤트 조회 기간 기본값: 오늘 0시 ~ 14일 후 0시
        now_kst = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9)))
        default_min = (now_kst.replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()
        default_max = (now_kst + dt.timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        me_events = await service.get_calendar_events(
            access_token=me_access,
            time_min=time_min or default_min,
            time_max=time_max or default_max,
        )
        friend_events = await service.get_calendar_events(
            access_token=friend_access,
            time_min=time_min or default_min,
            time_max=time_max or default_max,
        )

        # 바쁜 구간 추출 (dateTime 기준만 고려)
        def to_busy_intervals(events):
            intervals = []
            for e in events:
                try:
                    s = e.start.get("dateTime")
                    e_ = e.end.get("dateTime")
                    if not s or not e_:
                        continue
                    start = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                    end = dt.datetime.fromisoformat(e_.replace("Z", "+00:00"))
                    intervals.append((start, end))
                except Exception:
                    continue
            return intervals

        me_busy = to_busy_intervals(me_events)
        friend_busy = to_busy_intervals(friend_events)

        # 기간 경계
        min_boundary = dt.datetime.fromisoformat((time_min or default_min).replace("Z", "+00:00"))
        max_boundary = dt.datetime.fromisoformat((time_max or default_max).replace("Z", "+00:00"))

        # 병합 함수
        def merge(intervals):
            intervals = sorted(intervals, key=lambda x: x[0])
            merged = []
            for s, e in intervals:
                if not merged or s > merged[-1][1]:
                    merged.append([s, e])
                else:
                    merged[-1][1] = max(merged[-1][1], e)
            return [(s, e) for s, e in merged]

        # 바쁜 구간 합집합
        all_busy = merge(me_busy + friend_busy)

        # 전체 기간에서 바쁜 구간을 제외하여 free 구간 계산
        free = []
        cursor = min_boundary
        for s, e in all_busy:
            if e <= min_boundary:
                continue
            if s >= max_boundary:
                break
            if s > cursor:
                free.append((cursor, min(s, max_boundary)))
            cursor = max(cursor, e)
        if cursor < max_boundary:
            free.append((cursor, max_boundary))

        # duration 기준으로 슬롯 분할
        delta = dt.timedelta(minutes=duration_minutes)
        slots = []
        for s, e in free:
            t = s
            while t + delta <= e:
                slots.append({
                    "start": t.isoformat(),
                    "end": (t + delta).isoformat(),
                })
                t += delta

        return {"slots": slots}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"공통 가용 시간 계산 실패: {str(e)}")

@router.post("/meet-with-friend")
async def create_meeting_with_friend(
        payload: dict,
        current_user: dict = Depends(AuthService.get_current_user),
):
    """
    friend_id, summary, location, duration_minutes, time_min, time_max 를 받아
    공통 가용 시간 중 가장 이른 슬롯에 일정 생성.
    """
    try:
        friend_id = payload.get("friend_id")
        if not friend_id:
            raise HTTPException(status_code=400, detail="friend_id가 필요합니다.")
        summary = payload.get("summary", "만남")
        location = payload.get("location")
        duration_minutes = int(payload.get("duration_minutes", 60))
        time_min = payload.get("time_min")
        time_max = payload.get("time_max")

        # 공통 가용 슬롯 계산 재사용
        result = await get_common_free_slots(
            friend_id=friend_id,
            duration_minutes=duration_minutes,
            time_min=time_min,
            time_max=time_max,
            current_user=current_user,
        )
        slots = result.get("slots", [])
        if not slots:
            raise HTTPException(status_code=409, detail="공통 가용 시간이 없습니다.")

        slot = slots[0]
        start = slot["start"]
        end = slot["end"]

        # 이메일 수집 (참석자)
        me_email = current_user.get("email")
        friend = await AuthRepository.find_user_by_id(friend_id)
        if not friend:
            raise HTTPException(status_code=404, detail="친구 정보를 찾을 수 없습니다.")
        friend_email = friend.get("email")

        service = GoogleCalendarService()
        me_access = await _ensure_access_token(current_user)

        from .calender_models import CreateEventRequest
        event_req = CreateEventRequest(
            summary=summary,
            start_time=start,
            end_time=end,
            location=location,
            attendees=[e for e in [me_email, friend_email] if e],
        )

        event = await service.create_calendar_event(
            access_token=me_access,
            event_data=event_req,
        )
        return {"event": event}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"친구와 일정 생성 실패: {str(e)}")

# ---------------------------
# Google Calendar Webhook
# ---------------------------
@router.post("/webhook")
async def google_calendar_webhook(request: Request):
    try:
        headers = request.headers
        resource_state = headers.get("X-Goog-Resource-State")
        channel_id     = headers.get("X-Goog-Channel-Id")
        resource_id    = headers.get("X-Goog-Resource-Id")
        logger.info(f"[WEBHOOK] state={resource_state}, channel={channel_id}, resource={resource_id}")
        return {"status": "received"}
    except Exception as e:
        logger.error(f"[WEBHOOK] 웹훅 처리 오류: {str(e)}")
        raise HTTPException(status_code=400, detail=f"웹훅 처리 실패: {str(e)}")

@router.post("/subscribe")
async def subscribe_to_calendar_webhook(
        current_user: dict = Depends(AuthService.get_current_user),
        calendar_id: str = Query("primary", description="캘린더 ID")
):
    try:
        google_access_token = await _ensure_access_token(current_user)
        webhook_url = f"{settings.BASE_URL}/calendar/webhook"
        subscription_data = {
            "id": f"webhook_{current_user['email']}_{calendar_id}",
            "type": "web_hook",
            "address": webhook_url,
            "params": { "ttl": "2592000" }
        }
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch"
        headers = { "Authorization": f"Bearer {google_access_token}", "Content-Type": "application/json" }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=subscription_data, headers=headers)
            response.raise_for_status()
        result = response.json()
        logger.info(f"[WEBHOOK] 구독 성공: {result.get('id')}")
        return { "status": "success", "subscription_id": result.get("id"), "expiration": result.get("expiration") }
    except Exception as e:
        logger.error(f"[WEBHOOK] 구독 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"웹훅 구독 실패: {str(e)}")

@router.post("/renew-subscription")
async def renew_calendar_webhook(
        current_user: dict = Depends(AuthService.get_current_user),
        calendar_id: str = Query("primary", description="캘린더 ID")
):
    try:
        google_access_token = await _ensure_access_token(current_user)
        try:
            await unsubscribe_from_calendar_webhook(current_user, calendar_id, google_access_token)
        except Exception as e:
            logger.warning(f"[WEBHOOK] 기존 구독 해제 실패 (무시): {str(e)}")

        webhook_url = f"{settings.BASE_URL}/calendar/webhook"
        subscription_data = {
            "id": f"webhook_{current_user['email']}_{calendar_id}",
            "type": "web_hook",
            "address": webhook_url,
            "params": { "ttl": "2592000" }
        }
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch"
        headers = { "Authorization": f"Bearer {google_access_token}", "Content-Type": "application/json" }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=subscription_data, headers=headers)
            response.raise_for_status()
        result = response.json()
        logger.info(f"[WEBHOOK] 구독 갱신 성공: {result.get('id')}")
        return {
            "status": "success",
            "subscription_id": result.get("id"),
            "expiration": result.get("expiration"),
            "message": "웹훅 구독이 성공적으로 갱신되었습니다."
        }
    except Exception as e:
        logger.error(f"[WEBHOOK] 구독 갱신 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"웹훅 구독 갱신 실패: {str(e)}")

async def unsubscribe_from_calendar_webhook(current_user: dict, calendar_id: str, access_token: str):
    subscription_id = f"webhook_{current_user['email']}_{calendar_id}"
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/stop"
    headers = { "Authorization": f"Bearer {access_token}", "Content-Type": "application/json" }
    data = {"id": subscription_id}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=data, headers=headers)
        if response.status_code == 200:
            logger.info(f"[WEBHOOK] 구독 해제 성공: {subscription_id}")
        else:
            logger.warning(f"[WEBHOOK] 구독 해제 실패: {response.status_code}")

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
