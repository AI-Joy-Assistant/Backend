# src/calendar/router.py
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Optional
import datetime as dt
import httpx
import logging
import uuid

from .calender_models import CalendarEvent, CreateEventRequest, GoogleAuthRequest, GoogleAuthResponse
from .calender_service import GoogleCalendarService

from config.settings import settings
from config.database import supabase
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
# 캘린더 연동 상태 확인
# ---------------------------
@router.get("/link-status")
async def get_calendar_link_status(
    current_user: dict = Depends(AuthService.get_current_user)
):
    """
    현재 사용자의 Google 캘린더 연동 여부를 확인합니다.
    Apple 로그인 사용자가 캘린더를 연동했는지 확인하는 데 사용됩니다.
    """
    try:
        db_user = await AuthRepository.find_user_by_id(current_user["id"])
        if not db_user:
            return {"is_linked": False}
        
        # google_calendar_linked 필드 또는 refresh_token 존재 여부로 확인
        is_linked = db_user.get("google_calendar_linked", False) or bool(db_user.get("refresh_token"))
        
        return {"is_linked": is_linked}
    except Exception as e:
        logger.error(f"캘린더 연동 상태 확인 실패: {str(e)}")
        return {"is_linked": False}

# ---------------------------
# OAuth (선택 유지)
# ---------------------------
@router.get("/auth-url")
async def get_google_auth_url():
    service = GoogleCalendarService()
    auth_url = service.get_authorization_url()
    return {"auth_url": auth_url}

# ---------------------------
# 캘린더 연동 전용 (Apple 로그인 사용자용)
# ---------------------------
@router.get("/link-url")
async def get_calendar_link_url(
    current_user: dict = Depends(AuthService.get_current_user)
):
    """
    Apple 로그인 사용자가 Google 캘린더를 연동할 때 사용하는 OAuth URL 반환.
    state에 사용자 ID를 포함하여 콜백에서 어떤 사용자의 DB에 저장할지 알 수 있음.
    """
    import json
    from urllib.parse import urlencode
    
    scopes = [
        "openid", "email", "profile",
        "https://www.googleapis.com/auth/calendar",
    ]
    
    # state에 현재 사용자 ID 포함
    state_data = {
        "user_id": current_user["id"],
        "action": "calendar_link",
        "redirect_scheme": "frontend://calendar-linked"
    }
    
    # 콜백 URI 설정
    base_uri = settings.GOOGLE_REDIRECT_URI
    callback_uri = base_uri.replace('/auth/google/callback', '/calendar/link-callback')
    
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": callback_uri,
        "scope": " ".join(scopes),
        "response_type": "code",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": json.dumps(state_data),
    }
    
    qs = urlencode(params)
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{qs}"
    
    logger.info(f"캘린더 연동 URL 생성: user_id={current_user['id']}")
    return {"auth_url": auth_url, "redirect_uri": callback_uri}

@router.get("/link-callback")
async def calendar_link_callback(code: str, state: Optional[str] = None):
    """
    Google OAuth 콜백 - Apple 로그인 사용자의 DB에 Google 토큰 저장
    """
    import json
    from starlette.responses import RedirectResponse
    
    try:
        # state에서 사용자 ID 및 리다이렉트 스킴 추출
        user_id = None
        redirect_scheme = "frontend://calendar-linked"
        
        if state:
            try:
                state_data = json.loads(state)
                user_id = state_data.get("user_id")
                redirect_scheme = state_data.get("redirect_scheme", redirect_scheme)
            except:
                pass
        
        if not user_id:
            logger.error("캘린더 연동 콜백: user_id 없음")
            return RedirectResponse(url=f"{redirect_scheme}?error=no_user_id")
        
        logger.info(f"캘린더 연동 콜백 시작: user_id={user_id}")
        
        # Google 토큰 교환
        token_url = "https://oauth2.googleapis.com/token"
        base_uri = settings.GOOGLE_REDIRECT_URI
        callback_uri = base_uri.replace('/auth/google/callback', '/calendar/link-callback')
        
        token_data = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": callback_uri,
        }
        
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(token_url, data=token_data)
            token_response.raise_for_status()
            tokens = token_response.json()
        
        logger.info(f"캘린더 토큰 교환 성공: user_id={user_id}")
        
        # 토큰 만료 시각 계산
        expires_in = tokens.get("expires_in", 3600)
        token_expiry = (dt.datetime.utcnow() + dt.timedelta(seconds=expires_in)).isoformat()
        
        # 사용자 DB에 Google 토큰 저장
        await AuthRepository.update_user(
            user_id,
            {
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "token_expiry": token_expiry,
                "google_calendar_linked": True,
            }
        )
        
        logger.info(f"캘린더 연동 완료: user_id={user_id}")
        
        # 앱으로 리다이렉트 (성공)
        return RedirectResponse(url=f"{redirect_scheme}?success=true")
        
    except Exception as e:
        logger.error(f"캘린더 연동 콜백 오류: {str(e)}")
        return RedirectResponse(url=f"{redirect_scheme}?error={str(e)}")

@router.post("/auth", response_model=GoogleAuthResponse)
async def authenticate_google(
    request: GoogleAuthRequest,
    current_user: dict = Depends(AuthService.get_current_user)
):
    """
    Apple 로그인 사용자가 Google 캘린더를 연동할 때 사용.
    Google OAuth 코드로 토큰을 교환하고 현재 사용자의 DB에 저장.
    """
    try:
        service = GoogleCalendarService()
        token_data = await service.get_access_token(request.code)
        
        # 현재 로그인된 사용자의 DB에 Google 토큰 저장
        now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        new_expiry = (now_utc + dt.timedelta(seconds=token_data.get("expires_in", 3600))).isoformat()
        
        await AuthRepository.update_user(
            current_user["id"],
            {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "token_expiry": new_expiry,
                "google_calendar_linked": True,
            }
        )
        logger.info(f"Google 캘린더 토큰 저장 완료: {current_user['email']}")
        
        return GoogleAuthResponse(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data["expires_in"],
            token_type=token_data["token_type"],
        )
    except Exception as e:
        logger.error(f"Google 캘린더 인증 실패: {str(e)}")
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

@router.get("/busy-times", summary="특정 날짜의 바쁜 시간대 조회")
async def get_busy_times(
        date: str = Query(..., description="날짜 (YYYY-MM-DD 형식)"),
        current_user: dict = Depends(AuthService.get_current_user),
        calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """
    특정 날짜에 일정이 있는 시간대를 30분 단위로 반환합니다.
    재조율 시간 선택에서 비활성화할 시간대를 결정하는 데 사용됩니다.
    """
    try:
        google_access_token = await _ensure_access_token(current_user)
        service = GoogleCalendarService()
        
        # 해당 날짜의 시작/끝 시간 계산
        kst = dt.timezone(dt.timedelta(hours=9))
        date_obj = dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=kst)
        time_min = date_obj.replace(hour=0, minute=0, second=0).isoformat()
        time_max = date_obj.replace(hour=23, minute=59, second=59).isoformat()
        
        events = await service.get_calendar_events(
            access_token=google_access_token,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
        )
        
        # 바쁜 시간대 추출 (30분 단위)
        busy_slots = set()
        for event in events:
            start_str = event.start.get("dateTime") if hasattr(event, 'start') and event.start else None
            end_str = event.end.get("dateTime") if hasattr(event, 'end') and event.end else None
            
            if not start_str or not end_str:
                continue
            
            try:
                start = dt.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end = dt.datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                
                # 30분 단위로 바쁜 시간 추가
                current = start
                while current < end:
                    time_slot = current.astimezone(kst).strftime("%H:%M")
                    busy_slots.add(time_slot)
                    current += dt.timedelta(minutes=30)
            except Exception:
                continue
        
        return {"busy_times": sorted(list(busy_slots))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BUSY-TIMES] 조회 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"바쁜 시간대 조회 실패: {str(e)}")

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
        user_id = current_user["id"]
        logger.info(f"[CAL][DELETE][GOOGLE_ROUTE] 요청 시작 - user_id={user_id}, event_id={event_id}, calendar_id={calendar_id}")
        google_access_token = await _ensure_access_token(current_user)
        service = GoogleCalendarService()
        success = await service.delete_calendar_event(
            access_token=google_access_token,
            event_id=event_id,
            calendar_id=calendar_id,
        )
        logger.info(f"[CAL][DELETE][GOOGLE_ROUTE] Google API 삭제 결과 - event_id={event_id}, success={success}")
        # Google 삭제 성공/실패와 무관하게 로컬 DB 정합성 정리
        # - Google에서 이미 삭제된(404) 이벤트라도 DB 레코드가 남아 있으면 제거
        # - event_id가 row.id로 들어온 경우도 대비하여 id / google_event_id 둘 다 매칭
        # event_id가 UUID가 아닐 수 있으므로(id 컬럼은 uuid 타입) 안전하게 조건 구성
        is_uuid_event_id = False
        try:
            uuid.UUID(str(event_id))
            is_uuid_event_id = True
        except Exception:
            is_uuid_event_id = False

        query = supabase.table('calendar_event').select('id').eq('owner_user_id', user_id)
        if is_uuid_event_id:
            local_rows = query.or_(f"google_event_id.eq.{event_id},id.eq.{event_id}").execute()
        else:
            local_rows = query.eq('google_event_id', event_id).execute()
        local_ids = [row.get("id") for row in (local_rows.data or []) if row.get("id")]
        logger.info(f"[CAL][DELETE][GOOGLE_ROUTE] 로컬 정리 대상 - event_id={event_id}, local_ids={local_ids}")
        if local_ids:
            supabase.table('calendar_event').delete().in_('id', local_ids).execute()
            logger.info(f"[CAL][DELETE][GOOGLE_ROUTE] 로컬 DB 삭제 완료 - deleted_count={len(local_ids)}")

        if success or local_ids:
            logger.info(f"[CAL][DELETE][GOOGLE_ROUTE] 삭제 성공 응답 - event_id={event_id}, success={success}, local_deleted={len(local_ids)}")
            return {"message": "이벤트가 성공적으로 삭제되었습니다."}
        logger.warning(f"[CAL][DELETE][GOOGLE_ROUTE] 삭제 대상 없음 - event_id={event_id}")
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CAL][DELETE][GOOGLE_ROUTE] 예외 - event_id={event_id}, error={str(e)}")
        raise HTTPException(status_code=400, detail=f"이벤트 삭제 실패: {str(e)}")

# ---------------------------
# 앱 자체 캘린더 API (Google Calendar 미연동 사용자용)
# ---------------------------
import uuid

@router.get("/app-events", summary="앱 자체 캘린더 이벤트 조회")
async def get_app_calendar_events(
    current_user: dict = Depends(AuthService.get_current_user),
    time_min: Optional[str] = Query(None, description="ISO8601 시작 시간"),
    time_max: Optional[str] = Query(None, description="ISO8601 종료 시간"),
):
    """
    앱 자체 캘린더(calendar_event 테이블)에서 이벤트를 조회합니다.
    Google Calendar 연동 여부와 관계없이 사용 가능합니다.
    """
    try:
        user_id = current_user["id"]
        
        query = supabase.table('calendar_event').select('*').eq('owner_user_id', user_id)
        
        # 시간 필터 적용
        if time_min:
            query = query.gte('start_at', time_min)
        if time_max:
            query = query.lte('end_at', time_max)
        
        response = query.order('start_at', desc=False).execute()
        
        # Google Calendar 형식으로 변환
        events = []
        for row in response.data or []:
            # start_at, end_at을 Google Calendar 형식으로 변환
            start_at = row.get('start_at')
            end_at = row.get('end_at')
            
            event = {
                "id": row.get('id'),
                "summary": row.get('summary'),
                "location": row.get('location'),
                "start": {"dateTime": start_at} if start_at else {},
                "end": {"dateTime": end_at} if end_at else {},
                "htmlLink": row.get('html_link'),
                "status": row.get('status', 'confirmed'),
                "source": "app",  # 앱 자체 캘린더임을 표시
                "google_event_id": row.get('google_event_id'),
                "session_id": row.get('session_id'),
            }
            events.append(event)
        
        return {"events": events}
    except Exception as e:
        logger.error(f"앱 캘린더 이벤트 조회 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"이벤트 조회 실패: {str(e)}")


@router.post("/app-events", summary="앱 자체 캘린더에 이벤트 추가")
async def create_app_calendar_event(
    event_data: CreateEventRequest,
    current_user: dict = Depends(AuthService.get_current_user),
):
    """
    앱 자체 캘린더(calendar_event 테이블)에 이벤트를 추가합니다.
    Google Calendar 연동 여부와 관계없이 사용 가능합니다.
    """
    try:
        user_id = current_user["id"]
        
        # 시간 파싱 (KST 처리)
        def parse_datetime(s: str):
            if not s:
                return None
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            if "T" in s and "+" not in s and "Z" not in s:
                s += "+09:00"
            return dt.datetime.fromisoformat(s)
        
        start_dt = parse_datetime(event_data.start_time)
        end_dt = parse_datetime(event_data.end_time)
        
        if not start_dt or not end_dt:
            raise HTTPException(status_code=400, detail="시작/종료 시간이 필요합니다.")
        
        # UUID 형식의 고유 ID 생성 (Google event ID 대신)
        app_event_id = f"app_{uuid.uuid4().hex[:16]}"
        
        event_record = {
            "owner_user_id": user_id,
            "google_event_id": app_event_id,  # 앱 자체 이벤트 ID
            "summary": event_data.summary,
            "location": event_data.location,
            "start_at": start_dt.isoformat(),
            "end_at": end_dt.isoformat(),
            "time_zone": "Asia/Seoul",
            "status": "confirmed",
            "session_id": None,  # 개인 일정은 A2A 세션 없음
            "html_link": None
        }
        
        response = supabase.table('calendar_event').insert(event_record).execute()
        
        if response.data and len(response.data) > 0:
            created = response.data[0]
            logger.info(f"앱 캘린더 이벤트 생성 완료: {event_data.summary} (user: {user_id})")
            return {
                "id": created.get('id'),
                "summary": created.get('summary'),
                "start": {"dateTime": created.get('start_at')},
                "end": {"dateTime": created.get('end_at')},
                "location": created.get('location'),
                "source": "app"
            }
        
        raise HTTPException(status_code=400, detail="이벤트 생성에 실패했습니다.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"앱 캘린더 이벤트 생성 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"이벤트 생성 실패: {str(e)}")


@router.delete("/app-events/{event_id}", summary="앱 자체 캘린더 이벤트 삭제")
async def delete_app_calendar_event(
    event_id: str,
    current_user: dict = Depends(AuthService.get_current_user),
):
    """
    앱 자체 캘린더(calendar_event 테이블)에서 이벤트를 삭제합니다.
    본인의 이벤트만 삭제 가능합니다.
    """
    try:
        user_id = current_user["id"]
        logger.info(f"[CAL][DELETE][APP_ROUTE] 요청 시작 - user_id={user_id}, event_id={event_id}")
        
        # 소유자 확인
        existing = supabase.table('calendar_event').select('id, owner_user_id').eq('id', event_id).execute()
        logger.info(f"[CAL][DELETE][APP_ROUTE] 조회 결과 - found={len(existing.data or [])}")
        
        if not existing.data or len(existing.data) == 0:
            logger.warning(f"[CAL][DELETE][APP_ROUTE] 이벤트 없음 - user_id={user_id}, event_id={event_id}")
            raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")
        
        if existing.data[0].get('owner_user_id') != user_id:
            logger.warning(f"[CAL][DELETE][APP_ROUTE] 권한 없음 - owner={existing.data[0].get('owner_user_id')}, user_id={user_id}, event_id={event_id}")
            raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
        
        # 삭제
        supabase.table('calendar_event').delete().eq('id', event_id).execute()
        
        logger.info(f"[CAL][DELETE][APP_ROUTE] 삭제 완료 - user_id={user_id}, event_id={event_id}")
        return {"message": "이벤트가 성공적으로 삭제되었습니다."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CAL][DELETE][APP_ROUTE] 예외 - user_id={current_user.get('id')}, event_id={event_id}, error={str(e)}")
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
                    
                    # [✅ FIX] 종일 일정(date) 처리
                    if not s or not e_:
                        date_start = e.start.get("date")
                        date_end = e.end.get("date")
                        if date_start:
                            # 종일 일정 처리
                            # date_start 값 형식이 'YYYY-MM-DD'라고 가정
                            start_dt = dt.datetime.strptime(date_start, "%Y-%m-%d").replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
                            if date_end:
                                end_dt = dt.datetime.strptime(date_end, "%Y-%m-%d").replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
                            else:
                                end_dt = start_dt + dt.timedelta(days=1)
                            intervals.append((start_dt, end_dt))
                            continue
                        else:
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


# ---------------------------
# 다중 사용자 가용 시간 분석 (Request 화면용)
# ---------------------------
@router.post("/multi-user-free", summary="다중 사용자 공통 가용 시간 분석")
async def get_multi_user_free_slots(
        payload: dict,
        current_user: dict = Depends(AuthService.get_current_user),
):
    """
    여러 사용자의 캘린더를 분석하여 공통 가용 시간을 찾습니다.
    각 슬롯에 대해 참여 가능 인원수와 상태(최적/안정/협의 필요)를 반환합니다.
    
    Request body:
    - user_ids: list of user IDs to check availability
    - duration_minutes: meeting duration (default 60)
    - time_min: start of date range (ISO8601)
    - time_max: end of date range (ISO8601)
    - preferred_start_time: earliest meeting time (e.g., "09:00")
    - preferred_end_time: latest meeting end time (e.g., "18:00")
    """
    try:
        user_ids = payload.get("user_ids", [])
        duration_minutes = int(payload.get("duration_minutes", 60))
        time_min = payload.get("time_min")
        time_max = payload.get("time_max")
        preferred_start = payload.get("preferred_start_time", "09:00")  # e.g., "09:00"
        preferred_end = payload.get("preferred_end_time", "18:00")  # e.g., "18:00"
        limit = int(payload.get("limit", 5))
        duration_nights = int(payload.get("duration_nights", 0))  # ✅ 박 수 (0이면 당일, n이면 n박 n+1일)
        
        if not user_ids:
            raise HTTPException(status_code=400, detail="user_ids가 필요합니다.")
        
        # [FIX] 가상 사용자 ID 필터링 (tutorial_guide_joyner 등 UUID가 아닌 ID 제외)
        import uuid
        def is_valid_uuid(val):
            try:
                uuid.UUID(str(val))
                return True
            except ValueError:
                return False
        
        # UUID가 아닌 ID는 제외하고 경고 로그 출력
        invalid_ids = [uid for uid in user_ids if not is_valid_uuid(uid)]
        if invalid_ids:
            logger.warning(f"[MULTI-FREE] 유효하지 않은 사용자 ID 제외: {invalid_ids}")
        
        valid_user_ids = [uid for uid in user_ids if is_valid_uuid(uid)]
        
        # 현재 사용자 + 선택된 친구들 (유효한 ID만)
        all_user_ids = [current_user["id"]] + valid_user_ids
        total_participants = len(all_user_ids)
        
        service = GoogleCalendarService()
        kst = dt.timezone(dt.timedelta(hours=9))
        now_kst = dt.datetime.now(dt.timezone.utc).astimezone(kst)
        
        # 기본 조회 기간: 오늘 ~ 14일 후
        default_min = now_kst.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        default_max = (now_kst + dt.timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        query_time_min = time_min or default_min
        query_time_max = time_max or default_max
        
        # 각 사용자별 바쁜 시간 수집
        user_busy_map = {}  # user_id -> list of (start, end) tuples
        
        def to_busy_intervals(events):
            intervals = []
            for e in events:
                try:
                    s = e.start.get("dateTime")
                    e_ = e.end.get("dateTime")
                    
                    # [✅ FIX] 종일 일정(date) 처리
                    if not s or not e_:
                        date_start = e.start.get("date")
                        date_end = e.end.get("date")
                        if date_start:
                            # 종일 일정은 해당 날짜의 00:00 ~ 23:59:59 (또는 다음날 00:00)
                            # 구글 캘린더의 date_end는 다음날임 (exclusive)
                            start = dt.datetime.strptime(date_start, "%Y-%m-%d").replace(tzinfo=kst)
                            if date_end:
                                end = dt.datetime.strptime(date_end, "%Y-%m-%d").replace(tzinfo=kst)
                                # 종료 시간이 00:00이면 하루 전 23:59:59로 처리하면 좋지만,
                                # 여기서는 interval 비교를 위해 그대로 사용 (start < day_end and end > day_start 비교 시 안전)
                            else:
                                end = start + dt.timedelta(days=1)
                            intervals.append((start, end))
                            continue
                        else:
                            continue
                            
                    start = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                    end = dt.datetime.fromisoformat(e_.replace("Z", "+00:00"))
                    intervals.append((start, end))
                except Exception:
                    continue
            return intervals
        
        for user_id in all_user_ids:
            try:
                if user_id == current_user["id"]:
                    access_token = await _ensure_access_token(current_user)
                else:
                    access_token = await _ensure_access_token_by_user_id(user_id)
                
                events = await service.get_calendar_events(
                    access_token=access_token,
                    time_min=query_time_min,
                    time_max=query_time_max,
                )
                user_busy_map[user_id] = to_busy_intervals(events)
            except Exception as e:
                logger.warning(f"[MULTI-FREE] 사용자 {user_id} 캘린더 조회 실패: {e}")
                user_busy_map[user_id] = []  # 조회 실패 시 빈 리스트
        
        # 시간 경계 파싱
        min_boundary = dt.datetime.fromisoformat(query_time_min.replace("Z", "+00:00"))
        max_boundary = dt.datetime.fromisoformat(query_time_max.replace("Z", "+00:00"))
        
        # 선호 시간대 파싱 (HH:MM -> int)
        pref_start_h, pref_start_m = map(int, preferred_start.split(":"))
        pref_end_h, pref_end_m = map(int, preferred_end.split(":"))
        
        delta = dt.timedelta(minutes=duration_minutes)
        daynames = ['월', '화', '수', '목', '금', '토', '일']
        
        # 모든 가능한 슬롯 생성 (날짜별, 선호 시간대 내)
        all_candidates = []
        current_date = min_boundary.date()
        max_date = max_boundary.date()
        
        logger.info(f"[MULTI-FREE] 탐색 시작: {current_date} ~ {max_date}, 시간: {preferred_start}~{preferred_end}, 박수: {duration_nights}")

        # ✅ [여행 모드] duration_nights > 0이면 연속 일수 체크
        if duration_nights > 0:
            # 여행 모드: 시작 날짜별로 연속 N+1일 동안 모든 사용자의 가용성 체크
            needed_days = duration_nights + 1
            
            while current_date <= max_date:
                # 이 시작 날짜로부터 N+1일 동안의 날짜 목록 생성
                trip_dates = []
                for i in range(needed_days):
                    trip_date = current_date + dt.timedelta(days=i)
                    if trip_date > max_date:
                        break
                    trip_dates.append(trip_date)
                
                # 필요한 일수가 안 되면 스킵
                if len(trip_dates) < needed_days:
                    current_date += dt.timedelta(days=1)
                    continue
                
                # 각 사용자가 연속된 모든 날에 가능한지 확인
                available_users = []
                unavailable_users = []
                
                for user_id in all_user_ids:
                    busy_intervals = user_busy_map.get(user_id, [])
                    user_available_all_days = True
                    
                    # 모든 날짜에 대해 확인
                    for trip_date in trip_dates:
                        # ✅ [여행 모드] 해당 날짜의 전체 시간 범위 (00:00 ~ 23:59:59)
                        # 여행 중에는 하루 전체가 비어있어야 함
                        day_start = dt.datetime(
                            trip_date.year, trip_date.month, trip_date.day,
                            0, 0, 0, tzinfo=kst
                        )
                        day_end = dt.datetime(
                            trip_date.year, trip_date.month, trip_date.day,
                            23, 59, 59, tzinfo=kst
                        )
                        
                        # 이 날짜에 어떤 일정이라도 있으면 불가능
                        day_is_busy = False
                        for busy_start, busy_end in busy_intervals:
                            # 바쁜 시간이 이 날짜와 겹치면 불가능
                            if busy_start < day_end and busy_end > day_start:
                                day_is_busy = True
                                logger.info(f"[MULTI-FREE 여행] 사용자 {user_id}가 {trip_date}에 일정 있음: {busy_start} ~ {busy_end}")
                                break
                        
                        if day_is_busy:
                            user_available_all_days = False
                            break  # 하루라도 불가능하면 전체 불가능
                    
                    if user_available_all_days:
                        available_users.append(user_id)
                    else:
                        unavailable_users.append(user_id)
                
                available_count = len(available_users)
                half = total_participants / 2
                
                # 상태 결정
                if available_count == total_participants:
                    status = "최적"
                    score = 100
                elif available_count > half:
                    status = "안정"
                    score = 50 + available_count 
                else:
                    status = "협의 필요"
                    score = available_count
                
                # 후보 추가 (시작 날짜와 종료 날짜 모두 표시)
                start_date = trip_dates[0]
                end_date = trip_dates[-1]
                start_weekday = daynames[start_date.weekday()]
                end_weekday = daynames[end_date.weekday()]
                
                all_candidates.append({
                    "displayDate": f"{start_date.month}월 {start_date.day:02d}일 ({start_weekday}) ~ {end_date.month}월 {end_date.day:02d}일 ({end_weekday})",
                    "date": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "timeStart": preferred_start,
                    "timeEnd": preferred_end,
                    "availableCount": available_count,
                    "totalParticipants": total_participants,
                    "status": status,
                    "availableIds": available_users,
                    "unavailableIds": unavailable_users,
                    "score": score,
                    "datetime": dt.datetime(start_date.year, start_date.month, start_date.day, tzinfo=kst),
                    "duration_nights": duration_nights
                })
                
                current_date += dt.timedelta(days=1)
        else:
            # 일반 모드: 기존 로직 (단일 슬롯 체크)
            while current_date <= max_date:
                # 이 날짜의 선호 시간대 시작/끝
                day_start = dt.datetime(
                    current_date.year, current_date.month, current_date.day,
                    pref_start_h, pref_start_m, tzinfo=kst
                )
                day_end = dt.datetime(
                    current_date.year, current_date.month, current_date.day,
                    pref_end_h, pref_end_m, tzinfo=kst
                )
                
                # 슬롯 단위로 확인
                slot_start = day_start
                while slot_start + delta <= day_end:
                    # [Fix] 현재 시간보다 이전 슬롯 제거
                    if slot_start < now_kst:
                        slot_start += dt.timedelta(minutes=30)
                        continue

                    slot_end = slot_start + delta
                    
                    # 각 사용자가 이 슬롯에 가능한지 확인
                    available_users = []
                    unavailable_users = []
                    
                    for user_id in all_user_ids:
                        busy_intervals = user_busy_map.get(user_id, [])
                        is_busy = False
                        for busy_start, busy_end in busy_intervals:
                            # 슬롯과 바쁜 시간이 겹치는지 확인
                            if busy_start < slot_end and busy_end > slot_start:
                                is_busy = True
                                break
                        
                        if is_busy:
                            unavailable_users.append(user_id)
                        else:
                            available_users.append(user_id)
                    
                    available_count = len(available_users)
                    half = total_participants / 2
                    
                    # 상태 결정
                    if available_count == total_participants:
                        status = "최적"
                        score = 100
                    elif available_count > half:
                        status = "안정"
                        score = 50 + available_count 
                    else:
                        status = "협의 필요"
                        score = available_count
                    
                    # 후보 추가
                    weekday_idx = current_date.weekday()
                    day_name = daynames[weekday_idx]
                    
                    all_candidates.append({
                        "displayDate": f"{current_date.month}월 {current_date.day:02d}일 ({day_name})",
                        "date": current_date.isoformat(),
                        "timeStart": slot_start.strftime("%H:%M"),
                        "timeEnd": slot_end.strftime("%H:%M"),
                        "availableCount": available_count,
                        "totalParticipants": total_participants,
                        "status": status,
                        "availableIds": available_users,
                        "unavailableIds": unavailable_users,
                        "score": score,
                        "datetime": slot_start
                    })
                    
                    # 다음 슬롯 (30분 단위로 이동)
                    slot_start += dt.timedelta(minutes=30)
                
                current_date += dt.timedelta(days=1)
        
        logger.info(f"[MULTI-FREE] 후보 슬롯 {len(all_candidates)}개 발견")
        
        # 정렬: 점수 높은 순 -> 날짜 빠른 순 -> 시간 빠른 순
        all_candidates.sort(key=lambda x: (-x["score"], x["date"], x["timeStart"]))
        
        # 다양성 확보: 같은 날짜, 인접한 시간대는 피해서 상위 5개 선정
        final_recommendations = []
        selected_dates = {} # date -> count
        
        for cand in all_candidates:
            if len(final_recommendations) >= limit:
                break
                
            date_key = cand["date"]
            
            # 같은 날짜에 이미 2개 이상 추천되었으면 패스 (다양성)
            if selected_dates.get(date_key, 0) >= 2:
                continue
                
            # 이미 선택된 시간대와 너무 가까우면(1시간 이내) 패스
            is_too_close = False
            for selected in final_recommendations:
                if selected["date"] == date_key:
                    time_diff = abs((cand["datetime"] - selected["datetime"]).total_seconds())
                    if time_diff < 3600: # 1시간 미만 차이
                        is_too_close = True
                        break
            
            if is_too_close:
                continue
                
            # 선택 (datetime 유지)
            final_recommendations.append(cand)
            selected_dates[date_key] = selected_dates.get(date_key, 0) + 1
            
        # 결과가 limit 미만이면 남은 후보 중에서 채움 (단, 중복 제외)
        if len(final_recommendations) < limit:
            # datetime 객체는 비교가 어려우므로 식별자로 비교
            existing_keys = set(f"{r['date']}_{r['timeStart']}" for r in final_recommendations)
            for cand in all_candidates:
                if len(final_recommendations) >= limit:
                    break
                key = f"{cand['date']}_{cand['timeStart']}"
                if key not in existing_keys:
                    final_recommendations.append(cand)
                    existing_keys.add(key)
        
        # 최종 반환은 시간순 정렬 및 불필요 필드 제거
        final_recommendations.sort(key=lambda x: (x["date"], x["timeStart"]))
        
        # 클라이언트에 보낼 때는 datetime, score 제거
        cleaned_recommendations = []
        for r in final_recommendations:
            r_copy = r.copy()
            if "score" in r_copy: r_copy.pop("score")
            if "datetime" in r_copy: r_copy.pop("datetime")
            cleaned_recommendations.append(r_copy)

        return {"recommendations": cleaned_recommendations}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[MULTI-FREE] 다중 사용자 가용 시간 분석 실패: {str(e)}")
        raise HTTPException(status_code=400, detail=f"다중 사용자 가용 시간 분석 실패: {str(e)}")

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
