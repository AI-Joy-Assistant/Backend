from pydantic import BaseModel, Field
from typing import Optional


class IntentParseRequest(BaseModel):
    message: str = Field(..., description="사용자가 입력한 원문 메시지")


class IntentParseResult(BaseModel):
    intent: Optional[str] = Field(None, description="감지된 인텐트 (예: schedule)")
    friend_name: Optional[str] = Field(None, description="친구 이름 (1명인 경우)")
    friend_names: Optional[list] = Field(None, description="친구 이름 리스트 (여러 명인 경우)")
    date: Optional[str] = Field(None, description="날짜 표현")
    time: Optional[str] = Field(None, description="시간 표현")
    activity: Optional[str] = Field(None, description="활동/요청 요약")
    location: Optional[str] = Field(None, description="장소 정보")
    has_schedule_request: bool = Field(False, description="일정 관련 요청 여부")
    raw: Optional[dict] = Field(None, description="LLM 원본 응답 (디버깅용)")

