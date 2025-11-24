from pydantic import BaseModel
from typing import Optional, Dict, Any
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

class A2ASessionResponse(BaseModel):
    id: str
    initiator_user_id: str
    target_user_id: str
    status: str
    created_at: datetime

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



