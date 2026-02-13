import httpx
import json
import logging
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # py>=3.9

from config.settings import settings
from .calender_models import CalendarEvent, CreateEventRequest

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

def _to_rfc3339(value: Union[str, datetime, None]) -> Optional[str]:
    """
    timeMin/timeMax용 RFC3339 문자열로 변환.
    - 문자열이면 그대로 신뢰(이미 +09:00 또는 Z가 들어있다고 가정)
    - datetime이면 tz-aware 로 만들어서 isoformat()
    - None이면 None
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.isoformat()

class GoogleCalendarService:
    def __init__(self):
        self.base_url = "https://www.googleapis.com/calendar/v3"
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI

    async def get_access_token(self, authorization_code: str) -> dict:
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": authorization_code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(token_url, data=data)
                r.raise_for_status()
            token_data = r.json()
            # logger.info("Google OAuth 토큰 발급 성공")
            return token_data
        except httpx.HTTPStatusError as e:
            logger.error(f"OAuth 토큰 발급 실패: {e.response.status_code} - {e.response.text}")
            raise Exception(f"OAuth 토큰 발급 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"OAuth 토큰 발급 중 오류: {str(e)}")
            raise

    async def get_calendar_events(
            self,
            access_token: str,
            calendar_id: str = "primary",
            time_min: Optional[Union[str, datetime]] = None,
            time_max: Optional[Union[str, datetime]] = None
    ) -> List[CalendarEvent]:
        """
        구글 캘린더에서 이벤트를 가져옵니다.
        - time_min/time_max는 RFC3339 문자열(권장) 또는 datetime
        - 문자열이면 있는 그대로 전달 (+09:00 또는 Z 유지)
        - datetime이면 Asia/Seoul 기준 tz-aware 로 변환
        - Google의 timeMax는 '배타' 이므로, 일 조회는 다음날 00:00(+09:00), 월 조회는 다음달 1일 00:00(+09:00)를 주는 게 안전
        """
        if time_min is None:
            today_start_kst = datetime.now(tz=KST).replace(hour=0, minute=0, second=0, microsecond=0)
            time_min = today_start_kst
        if time_max is None:
            time_max = (datetime.now(tz=KST) + timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)

        time_min_str = _to_rfc3339(time_min)
        time_max_str = _to_rfc3339(time_max)

        url = f"{self.base_url}/calendars/{calendar_id}/events"
        params = {
            "timeMin": time_min_str,
            "timeMax": time_max_str,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
            "timeZone": "Asia/Seoul",   # ★ 경계 판정 타임존 고정
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # logger.info(f"[CAL][LIST] GET {url} params={params}")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(url, params=params, headers=headers)
                r.raise_for_status()
            data = r.json()

            items = data.get("items", [])
            events: List[CalendarEvent] = []
            for item in items:
                try:
                    events.append(CalendarEvent(
                        id=item["id"],
                        summary=item.get("summary", "제목 없음"),
                        description=item.get("description"),
                        start=item.get("start", {}),
                        end=item.get("end", {}),
                        attendees=item.get("attendees", []),
                        location=item.get("location"),
                        htmlLink=item.get("htmlLink"),
                    ))
                except Exception as e:
                    logger.warning(f"이벤트 파싱 실패: {str(e)}, 원본: {json.dumps(item)[:300]}")

            # logger.info(f"[CAL][LIST] {len(events)}개 이벤트 조회 성공")
            return events

        except httpx.HTTPStatusError as e:
            logger.error(f"Calendar LIST 실패: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("인증 토큰 만료 또는 유효하지 않음")
            if e.response.status_code == 403:
                raise Exception("캘린더 접근 권한 없음")
            raise Exception(f"Google Calendar API 오류: {e.response.status_code}")
        except Exception as e:
            logger.error(f"이벤트 조회 중 오류: {str(e)}")
            raise

    async def create_calendar_event(
            self,
            access_token: str,
            event_data: CreateEventRequest,
            calendar_id: str = "primary"
    ) -> CalendarEvent:
        url = f"{self.base_url}/calendars/{calendar_id}/events"

        try:
            start_time_str = event_data.start_time
            end_time_str = event_data.end_time

            def parse_iso(s: str) -> datetime:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                if "T" in s and "+" not in s and "Z" not in s:
                    s += "+09:00"
                return datetime.fromisoformat(s)

            start_dt = parse_iso(start_time_str)
            end_dt = parse_iso(end_time_str)

        except Exception as e:
            logger.error(f"날짜 파싱 실패: start={event_data.start_time}, end={event_data.end_time}, {e}")
            raise Exception(f"잘못된 날짜 형식: {str(e)}")

        event_body: Dict[str, Any] = {
            "summary": event_data.summary,
        }
        
        # 종일 일정은 date 형식 사용, 일반 일정은 dateTime 형식 사용
        if event_data.is_all_day:
            # 날짜만 추출 (YYYY-MM-DD)
            start_date = start_dt.strftime("%Y-%m-%d")
            end_date = end_dt.strftime("%Y-%m-%d")
            event_body["start"] = {"date": start_date, "timeZone": "Asia/Seoul"}
            event_body["end"] = {"date": end_date, "timeZone": "Asia/Seoul"}
        else:
            event_body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Seoul"}
            event_body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"}
        if getattr(event_data, "description", None):
            event_body["description"] = event_data.description
        if getattr(event_data, "location", None):
            event_body["location"] = event_data.location
        if getattr(event_data, "attendees", None):
            event_body["attendees"] = [{"email": e} for e in (event_data.attendees or [])]

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # logger.info(f"[CAL][CREATE] POST {url} body={json.dumps(event_body)[:400]}")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(url, json=event_body, headers=headers)
                r.raise_for_status()
            data = r.json()
            evt = CalendarEvent(
                id=data["id"],
                summary=data.get("summary", ""),
                description=data.get("description"),
                start=data.get("start", {}),
                end=data.get("end", {}),
                attendees=data.get("attendees"),
                location=data.get("location"),
                htmlLink=data.get("htmlLink"),
            )
            # logger.info(f"[CAL][CREATE] 성공: {evt.summary} ({evt.id})")
            return evt

        except httpx.HTTPStatusError as e:
            logger.error(f"Calendar CREATE 실패: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("인증 토큰 만료. 다시 로그인 필요.")
            if e.response.status_code == 403:
                raise Exception("이벤트 생성 권한 없음 (스코프 미승인?)")
            raise Exception(f"이벤트 생성 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"이벤트 생성 중 오류: {str(e)}")
            raise

    async def delete_calendar_event(self, access_token: str, event_id: str, calendar_id: str = "primary") -> bool:
        url = f"{self.base_url}/calendars/{calendar_id}/events/{event_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        logger.info(f"[CAL][DELETE][GOOGLE_API] 요청 - calendar_id={calendar_id}, event_id={event_id}, url={url}")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.delete(url, headers=headers)
            logger.info(f"[CAL][DELETE][GOOGLE_API] 응답 - event_id={event_id}, status={r.status_code}")
            if r.status_code in (200, 204):
                logger.info(f"[CAL][DELETE][GOOGLE_API] 성공 - event_id={event_id}")
                return True
            logger.error(f"[CAL][DELETE] 실패: {r.status_code} - {r.text}")
            return False
        except httpx.HTTPStatusError as e:
            logger.error(f"[CAL][DELETE] 실패: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("인증 토큰 만료. 다시 로그인 필요.")
            if e.response.status_code == 403:
                raise Exception("삭제 권한 없음")
            if e.response.status_code == 404:
                raise Exception("이벤트를 찾을 수 없음")
            raise Exception(f"이벤트 삭제 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"[CAL][DELETE] 오류: {str(e)}")
            raise

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        scopes = [
            "openid", "email", "profile",
            "https://www.googleapis.com/auth/calendar",
        ]
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "response_type": "code",
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        from urllib.parse import urlencode
        qs = urlencode(params)
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{qs}"
        # logger.info(f"Google OAuth 인증 URL 생성: {auth_url}")
        return auth_url

    async def refresh_access_token(self, refresh_token: str) -> dict:
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(token_url, data=data)
                r.raise_for_status()
            token_data = r.json()
            # logger.info("Google OAuth 토큰 갱신 성공")
            return token_data
        except httpx.HTTPStatusError as e:
            logger.error(f"토큰 갱신 실패: {e.response.status_code} - {e.response.text}")
            raise Exception(f"토큰 갱신 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"토큰 갱신 중 오류: {str(e)}")
            raise

class CalendarService:
    """캘린더 서비스 - 사용자 인증 및 이벤트 관리"""
    
    @staticmethod
    async def create_event(user_id: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """사용자 ID로 일정 생성"""
        try:
            from src.auth.auth_service import AuthService
            
            # 사용자 정보 조회 (Google 액세스 토큰 포함)
            user_info = await AuthService.get_user_by_id(user_id)
            if not user_info:
                return {"status": 404, "error": "사용자를 찾을 수 없습니다."}
            
            access_token = user_info.get("access_token")
            if not access_token:
                return {"status": 401, "error": "Google Calendar 연동이 필요합니다."}
            
            # GoogleCalendarService 인스턴스 생성
            google_calendar = GoogleCalendarService()
            
            # CreateEventRequest 객체 생성
            from .calender_models import CreateEventRequest
            create_request = CreateEventRequest(
                summary=event_data.get("summary", "새 일정"),
                description=event_data.get("description", ""),
                start_time=event_data.get("start_time"),
                end_time=event_data.get("end_time"),
                location=event_data.get("location", ""),
                attendees=event_data.get("attendees", [])
            )
            
            # Google Calendar에 이벤트 생성
            calendar_event = await google_calendar.create_calendar_event(
                access_token=access_token,
                event_data=create_request
            )
            
            return {
                "status": 200,
                "data": {
                    "id": calendar_event.id,
                    "summary": calendar_event.summary,
                    "description": calendar_event.description,
                    "start": calendar_event.start,
                    "end": calendar_event.end,
                    "location": calendar_event.location,
                    "htmlLink": calendar_event.htmlLink
                }
            }
            
        except Exception as e:
            logger.error(f"일정 생성 실패: {str(e)}")
            return {"status": 500, "error": f"일정 생성 실패: {str(e)}"}
