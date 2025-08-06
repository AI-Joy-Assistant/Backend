from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from datetime import datetime
from .models import CalendarEvent, CreateEventRequest, GoogleAuthRequest, GoogleAuthResponse
from .service import GoogleCalendarService

router = APIRouter(prefix="/calendar", tags=["calendar"])

@router.get("/auth-url")
async def get_google_auth_url():
    """Google OAuth2 인증 URL을 반환합니다."""
    service = GoogleCalendarService()
    auth_url = service.get_authorization_url()
    return {"auth_url": auth_url}

@router.post("/auth", response_model=GoogleAuthResponse)
async def authenticate_google(request: GoogleAuthRequest):
    """Google OAuth2 인증 코드로 액세스 토큰을 받아옵니다."""
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

@router.get("/events")
async def get_calendar_events(
    access_token: Optional[str] = Query(None, description="Google 액세스 토큰"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
    time_min: Optional[datetime] = Query(None, description="시작 시간"),
    time_max: Optional[datetime] = Query(None, description="종료 시간"),
):
    """구글 캘린더에서 이벤트를 가져옵니다."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Google 액세스 토큰이 필요합니다.")
    
    try:
        # 실제 Google Calendar API 호출
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
    access_token: str = Query(..., description="Google 액세스 토큰"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """구글 캘린더에 새 이벤트를 생성합니다."""
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
    access_token: str = Query(..., description="Google 액세스 토큰"),
    calendar_id: str = Query("primary", description="캘린더 ID"),
):
    """구글 캘린더에서 이벤트를 삭제합니다."""
    try:
        service = GoogleCalendarService()
        success = await service.delete_calendar_event(
            access_token=access_token,
            event_id=event_id,
            calendar_id=calendar_id,
        )
        
        if success:
            return {"message": "이벤트가 성공적으로 삭제되었습니다."}
        else:
            raise HTTPException(status_code=400, detail="이벤트 삭제에 실패했습니다.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이벤트 삭제 실패: {str(e)}")

@router.get("/test")
async def test_calendar_api():
    """캘린더 API 테스트용 엔드포인트"""
    return {
        "message": "Google Calendar API 연결 성공",
        "status": "ready",
        "available_endpoints": [
            "GET /calendar/auth-url",
            "POST /calendar/auth", 
            "GET /calendar/events",
            "POST /calendar/events",
            "DELETE /calendar/events/{event_id}"
        ]
    } 