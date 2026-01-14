from pydantic import BaseModel, Field
from typing import Optional


class IntentParseRequest(BaseModel):
    message: str = Field(..., description="사용자가 입력한 원문 메시지")


class IntentParseResult(BaseModel):
    intent: Optional[str] = Field(None, description="감지된 인텐트 (예: schedule)")
    friend_name: Optional[str] = Field(None, description="친구 이름 (1명인 경우)")
    friend_names: Optional[list] = Field(None, description="친구 이름 리스트 (여러 명인 경우)")
    date: Optional[str] = Field(None, description="날짜 표현")
    start_date: Optional[str] = Field(None, description="시작 날짜 (YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="종료 날짜 (YYYY-MM-DD)")
    time: Optional[str] = Field(None, description="시간 표현")
    start_time: Optional[str] = Field(None, description="시작 시간 (HH:MM)")
    end_time: Optional[str] = Field(None, description="종료 시간 (HH:MM)")
    activity: Optional[str] = Field(None, description="활동/요청 요약")
    title: Optional[str] = Field(None, description="일정 제목")
    location: Optional[str] = Field(None, description="장소 정보")
    has_schedule_request: bool = Field(False, description="일정 관련 요청 여부")
    missing_fields: Optional[list] = Field(None, description="누락된 필수 정보 리스트")
    raw: Optional[dict] = Field(None, description="LLM 원본 응답 (디버깅용)")

