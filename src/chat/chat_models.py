from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

# 채팅 메시지 모델 (a2a 테이블)
class ChatMessageBase(BaseModel):
    send_id: uuid.UUID
    receive_id: uuid.UUID
    message: str
    message_type: str = "text"  # text, schedule, system 등

class ChatMessageCreate(ChatMessageBase):
    pass

class ChatMessage(ChatMessageBase):
    id: uuid.UUID
    created_at: datetime
    
    class Config:
        from_attributes = True

# 채팅방 모델
class ChatRoom(BaseModel):
    participants: List[uuid.UUID]  # 참여자들의 user_id
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    participant_names: List[str] = []  # 참여자들의 이름

class ChatRoomWithMessages(ChatRoom):
    messages: List[ChatMessage] = []

# AI 채팅 로그 모델 (chat_log 테이블)
class ChatLogBase(BaseModel):
    user_id: uuid.UUID
    request_text: Optional[str] = None
    response_text: Optional[str] = None
    session_id: Optional[uuid.UUID] = None

class ChatLogCreate(ChatLogBase):
    pass

class ChatLog(ChatLogBase):
    id: uuid.UUID
    created_at: datetime
    
    class Config:
        from_attributes = True

# API 응답 모델들
class ChatRoomListResponse(BaseModel):
    chat_rooms: List[ChatRoom]

class ChatMessagesResponse(BaseModel):
    messages: List[ChatMessage]
    
class SendMessageRequest(BaseModel):
    receive_id: uuid.UUID
    message: str
    message_type: str = "text" 

# AI Chat 요청 모델
class AIChatRequest(BaseModel):
    message: str
    selected_friends: Optional[List[str]] = None
    session_id: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    duration_nights: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: int = 60
    is_all_day: bool = False