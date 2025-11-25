from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class CalendarEvent(BaseModel):
    id: str
    summary: str
    description: Optional[str] = None
    start: dict
    end: dict
    attendees: Optional[List[dict]] = None
    location: Optional[str] = None
    htmlLink: Optional[str] = None

class CreateEventRequest(BaseModel):
    summary: str
    description: Optional[str] = None
    start_time: str  # ISO 8601 형식의 문자열
    end_time: str    # ISO 8601 형식의 문자열
    attendees: Optional[List[str]] = None
    location: Optional[str] = None

class GoogleAuthRequest(BaseModel):
    code: str
    redirect_uri: str

class GoogleAuthResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int
    token_type: str 