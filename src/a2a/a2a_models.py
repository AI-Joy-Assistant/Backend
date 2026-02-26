from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

class A2ASessionStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class A2AMessageType(str, Enum):
    AGENT_QUERY = "agent_query"
    AGENT_REPLY = "agent_reply"
    PROPOSAL = "proposal"
    CONFIRM = "confirm"
    SYSTEM = "system"
    FINAL = "final"

class A2ASessionCreate(BaseModel):
    target_user_id: str
    intent: str = "schedule"
    time_window: Optional[Dict[str, Any]] = None
    place_pref: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    origin_chat_session_id: Optional[str] = None  # 일정 요청을 시작한 원본 채팅방 ID

class A2ASessionResponse(BaseModel):
    id: str
    initiator_user_id: str
    target_user_id: str
    status: str
    created_at: datetime
    thread_id: Optional[str] = None
    participant_count: Optional[int] = None
    participant_ids: Optional[List[str]] = None
    participant_names: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None
    title: Optional[str] = None
    summary: Optional[str] = None

class A2AMessageCreate(BaseModel):
    session_id: str
    sender_user_id: str
    receiver_user_id: str
    message_type: str
    message: Dict[str, Any]

class A2AMessageResponse(BaseModel):
    id: str
    session_id: str
    sender_user_id: str
    receiver_user_id: str
    message_type: str
    message: Dict[str, Any]
    created_at: datetime


class A2AQuickCreateRequest(BaseModel):
    participant_user_ids: List[str]
    title: str
    start_date: str  # YYYY-MM-DD
    start_time: Optional[str] = None  # HH:MM
    end_date: Optional[str] = None  # YYYY-MM-DD
    end_time: Optional[str] = None  # HH:MM
    location: Optional[str] = None
    is_all_day: bool = False
    duration_minutes: int = 60
    duration_nights: int = 0


class A2AQuickCreateResponse(BaseModel):
    thread_id: str
    session_ids: List[str]
    status: str = "in_progress"


