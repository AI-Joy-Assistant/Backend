from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime

class FriendRequest(BaseModel):
    id: str
    from_user_id: str
    from_user_name: str
    from_user_email: str
    from_user_picture: Optional[str] = None
    to_user_id: str
    status: str  # "pending", "accepted", "rejected"
    created_at: datetime

class Friend(BaseModel):
    id: str
    user_id: str
    friend_id: str
    friend_name: str
    friend_email: str
    friend_picture: Optional[str] = None
    created_at: datetime

class AddFriendRequest(BaseModel):
    email: str  # 이메일 또는 handle 모두 허용

class FriendRequestResponse(BaseModel):
    id: str
    from_user: dict
    status: str
    created_at: datetime

class FriendResponse(BaseModel):
    id: str
    friend: dict
    created_at: datetime

class FriendListResponse(BaseModel):
    friends: List[FriendResponse]
    total_count: int

class FriendRequestListResponse(BaseModel):
    requests: List[FriendRequestResponse]
    total_count: int

class SearchFriendResponse(BaseModel):
    users: List[dict]
    total_count: int

class MessageResponse(BaseModel):
    message: str 