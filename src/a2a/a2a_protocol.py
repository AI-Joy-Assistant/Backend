"""
A2A Protocol - 에이전트 간 통신 프로토콜 정의
"""
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from enum import Enum


class MessageType(str, Enum):
    """에이전트 메시지 타입"""
    PROPOSE = "PROPOSE"           # 시간 제안
    ACCEPT = "ACCEPT"             # 제안 수락
    REJECT = "REJECT"             # 제안 거절
    COUNTER = "COUNTER"           # 역제안
    QUERY = "QUERY"               # 가용시간 질의
    NEED_HUMAN = "NEED_HUMAN"     # 사용자 개입 필요
    INFO = "INFO"                 # 정보 전달 (진행 상황 등)
    # 새로운 메시지 타입
    CONFLICT_CHOICE = "CONFLICT_CHOICE"       # 충돌 시 선택 요청 (참석불가/일정조정)
    AWAITING_CHOICE = "AWAITING_CHOICE"       # 사용자 선택 대기 중
    MAJORITY_RECOMMEND = "MAJORITY_RECOMMEND" # 과반수 추천


class ConflictInfo(BaseModel):
    """충돌 일정 정보"""
    event_name: str                   # Google Calendar 일정명 (예: "치과 예약")
    event_start: Optional[datetime] = None
    event_end: Optional[datetime] = None
    event_time_display: Optional[str] = None  # "오후 6시~7시" 형식


class ParticipantAvailability(BaseModel):
    """참여자별 가용성 정보"""
    user_id: str
    user_name: str
    is_available: bool
    conflict_info: Optional[ConflictInfo] = None  # 불가능 시 충돌 정보
    choice: Optional[str] = None  # "skip" | "adjust" | None (미선택)


class MajorityRecommendation(BaseModel):
    """과반수 추천 정보"""
    date: str                          # "12월 17일"
    time_condition: str                # "오후 6시 이후"
    available_count: int               # 3
    total_count: int                   # 4
    available_names: List[str]         # ["규민", "민서", "수연"]
    unavailable_names: List[str]       # ["호이"]
    is_majority: bool                  # 과반수 여부


class TimeSlot(BaseModel):
    """시간 슬롯"""
    start: datetime
    end: datetime
    
    def overlaps(self, other: "TimeSlot") -> bool:
        """다른 슬롯과 겹치는지 확인"""
        return self.start < other.end and other.start < self.end
    
    def to_display_string(self) -> str:
        """표시용 문자열"""
        return self.start.strftime("%m월 %d일 %H:%M")


class Proposal(BaseModel):
    """일정 제안"""
    date: str                     # "2024-12-15" 형식
    time: str                     # "14:00" 형식
    time_slot: Optional[TimeSlot] = None
    location: Optional[str] = None
    activity: Optional[str] = None
    duration_minutes: int = 60
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "time": self.time,
            "location": self.location,
            "activity": self.activity,
            "duration_minutes": self.duration_minutes
        }


class AgentDecision(BaseModel):
    """에이전트의 결정"""
    action: MessageType
    proposal: Optional[Proposal] = None
    reason: Optional[str] = None
    message: str                  # GPT가 생성한 자연어 메시지
    available_slots: Optional[List[TimeSlot]] = None
    # 새로운 필드
    conflict_info: Optional[ConflictInfo] = None  # 충돌 정보


class A2AMessage(BaseModel):
    """에이전트 간 통신 메시지"""
    id: str
    session_id: str
    type: MessageType
    sender_agent_id: str          # user_id 기반
    sender_name: str
    round_number: int
    proposal: Optional[Proposal] = None
    message: str                  # 자연어 메시지
    available_slots: Optional[List[Dict]] = None
    timestamp: datetime
    # 새로운 필드
    conflict_info: Optional[Dict] = None           # 충돌 정보 (직렬화용)
    majority_recommendation: Optional[Dict] = None # 과반수 추천 정보
    participant_availabilities: Optional[List[Dict]] = None  # 참여자별 가용성
    
    def to_sse_data(self) -> Dict[str, Any]:
        """SSE 스트리밍용 데이터 변환"""
        return {
            "id": self.id,
            "type": self.type.value,
            "sender_name": self.sender_name,
            "round": self.round_number,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "conflict_info": self.conflict_info,
            "majority_recommendation": self.majority_recommendation,
            "participant_availabilities": self.participant_availabilities
        }


class NegotiationStatus(str, Enum):
    """협상 상태"""
    IN_PROGRESS = "in_progress"
    AGREED = "agreed"
    FAILED = "failed"
    NEED_HUMAN = "need_human"
    # 새로운 상태
    AWAITING_USER_CHOICE = "awaiting_user_choice"  # 사용자 선택 대기


class HumanInterventionReason(str, Enum):
    """사용자 개입 필요 사유"""
    MAX_ROUNDS_EXCEEDED = "max_rounds_exceeded"      # 5라운드 초과
    DEADLOCK = "deadlock"                            # 교착 상태
    NO_COMMON_TIME = "no_common_time"                # 공통 시간 없음
    URGENT_SCHEDULE = "urgent_schedule"              # 긴급 일정 (24시간 이내)
    # 새로운 사유
    CONFLICT_CHOICE_NEEDED = "conflict_choice_needed"  # 충돌 선택 필요


class NegotiationResult(BaseModel):
    """협상 결과"""
    status: NegotiationStatus
    final_proposal: Optional[Proposal] = None
    agreed_by: Optional[List[str]] = None           # 동의한 user_id 목록
    intervention_reason: Optional[HumanInterventionReason] = None
    total_rounds: int = 0
    messages: List[A2AMessage] = []
    # 새로운 필드
    awaiting_choice_from: Optional[List[str]] = None  # 선택 대기 중인 user_id 목록
    majority_recommendations: Optional[List[Dict]] = None  # 과반수 추천 목록
