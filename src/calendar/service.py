import httpx
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from config.settings import settings
from .models import CalendarEvent, CreateEventRequest

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GoogleCalendarService:
    def __init__(self):
        self.base_url = "https://www.googleapis.com/calendar/v3"
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI

    async def get_access_token(self, authorization_code: str) -> dict:
        """Google OAuth2 인증 코드로 액세스 토큰을 받아옵니다."""
        token_url = "https://oauth2.googleapis.com/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": authorization_code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=data)
                response.raise_for_status()
                token_data = response.json()
                logger.info("Google OAuth 토큰 발급 성공")
                return token_data
        except httpx.HTTPStatusError as e:
            logger.error(f"Google OAuth 토큰 발급 실패: {e.response.status_code} - {e.response.text}")
            raise Exception(f"OAuth 토큰 발급 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Google OAuth 토큰 발급 중 오류: {str(e)}")
            raise

    async def get_calendar_events(self, access_token: str, calendar_id: str = "primary", 
                                time_min: Optional[datetime] = None, 
                                time_max: Optional[datetime] = None) -> List[CalendarEvent]:
        """구글 캘린더에서 이벤트를 가져옵니다."""
        if not time_min:
            time_min = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if not time_max:
            time_max = time_min + timedelta(days=30)

        url = f"{self.base_url}/calendars/{calendar_id}/events"
        params = {
            "timeMin": time_min.isoformat() + "Z",
            "timeMax": time_max.isoformat() + "Z",
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 100,  # 최대 100개 이벤트
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                events = []
                
                for item in data.get("items", []):
                    try:
                        event = CalendarEvent(
                            id=item["id"],
                            summary=item.get("summary", "제목 없음"),
                            description=item.get("description"),
                            start=item["start"],
                            end=item["end"],
                            attendees=item.get("attendees", []),
                            location=item.get("location"),
                            htmlLink=item.get("htmlLink"),
                        )
                        events.append(event)
                    except Exception as e:
                        logger.warning(f"이벤트 파싱 실패: {str(e)}, 이벤트 ID: {item.get('id')}")
                        continue
                
                logger.info(f"Google Calendar에서 {len(events)}개 이벤트 조회 성공")
                return events
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Google Calendar API 호출 실패: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("인증 토큰이 만료되었습니다. 다시 로그인해주세요.")
            elif e.response.status_code == 403:
                raise Exception("캘린더 접근 권한이 없습니다.")
            else:
                raise Exception(f"Google Calendar API 오류: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Google Calendar 이벤트 조회 중 오류: {str(e)}")
            raise

    async def create_calendar_event(self, access_token: str, event_data: CreateEventRequest, 
                                  calendar_id: str = "primary") -> CalendarEvent:
        """구글 캘린더에 새 이벤트를 생성합니다."""
        url = f"{self.base_url}/calendars/{calendar_id}/events"
        
        event_body = {
            "summary": event_data.summary,
            "description": event_data.description,
            "start": {
                "dateTime": event_data.start_time.isoformat(),
                "timeZone": "Asia/Seoul",
            },
            "end": {
                "dateTime": event_data.end_time.isoformat(),
                "timeZone": "Asia/Seoul",
            },
        }

        if event_data.location:
            event_body["location"] = event_data.location

        if event_data.attendees:
            event_body["attendees"] = [
                {"email": email} for email in event_data.attendees
            ]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=event_body, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                event = CalendarEvent(
                    id=data["id"],
                    summary=data["summary"],
                    description=data.get("description"),
                    start=data["start"],
                    end=data["end"],
                    attendees=data.get("attendees"),
                    location=data.get("location"),
                    htmlLink=data.get("htmlLink"),
                )
                
                logger.info(f"Google Calendar 이벤트 생성 성공: {event.summary}")
                return event
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Google Calendar 이벤트 생성 실패: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("인증 토큰이 만료되었습니다. 다시 로그인해주세요.")
            elif e.response.status_code == 403:
                raise Exception("캘린더에 이벤트를 생성할 권한이 없습니다.")
            else:
                raise Exception(f"이벤트 생성 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Google Calendar 이벤트 생성 중 오류: {str(e)}")
            raise

    async def delete_calendar_event(self, access_token: str, event_id: str, 
                                  calendar_id: str = "primary") -> bool:
        """구글 캘린더에서 이벤트를 삭제합니다."""
        url = f"{self.base_url}/calendars/{calendar_id}/events/{event_id}"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(url, headers=headers)
                if response.status_code == 204:
                    logger.info(f"Google Calendar 이벤트 삭제 성공: {event_id}")
                    return True
                else:
                    logger.error(f"Google Calendar 이벤트 삭제 실패: {response.status_code}")
                    return False
                    
        except httpx.HTTPStatusError as e:
            logger.error(f"Google Calendar 이벤트 삭제 실패: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                raise Exception("인증 토큰이 만료되었습니다. 다시 로그인해주세요.")
            elif e.response.status_code == 403:
                raise Exception("캘린더에서 이벤트를 삭제할 권한이 없습니다.")
            elif e.response.status_code == 404:
                raise Exception("삭제할 이벤트를 찾을 수 없습니다.")
            else:
                raise Exception(f"이벤트 삭제 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Google Calendar 이벤트 삭제 중 오류: {str(e)}")
            raise

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Google OAuth2 인증 URL을 생성합니다."""
        scopes = [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ]
        
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
        
        if state:
            params["state"] = state
            
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{query_string}"
        logger.info(f"Google OAuth 인증 URL 생성: {auth_url}")
        return auth_url

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """리프레시 토큰으로 새로운 액세스 토큰을 받아옵니다."""
        token_url = "https://oauth2.googleapis.com/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=data)
                response.raise_for_status()
                token_data = response.json()
                logger.info("Google OAuth 토큰 갱신 성공")
                return token_data
        except httpx.HTTPStatusError as e:
            logger.error(f"Google OAuth 토큰 갱신 실패: {e.response.status_code} - {e.response.text}")
            raise Exception(f"토큰 갱신 실패: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Google OAuth 토큰 갱신 중 오류: {str(e)}")
            raise 