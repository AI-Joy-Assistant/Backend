"""
PersonalAgent - 각 사용자별 독립 AI 에이전트
"""
import logging
import json
import re
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.chat.chat_openai_service import OpenAIService
from src.auth.auth_repository import AuthRepository
from src.auth.auth_service import AuthService
from src.calendar.calender_service import GoogleCalendarService
from .a2a_protocol import (
    MessageType, TimeSlot, Proposal, AgentDecision, A2AMessage
)

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# 요일 한글 변환
WEEKDAY_KR = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

def _get_weekday_korean(date_str: str) -> str:
    """날짜 문자열(YYYY-MM-DD)을 한글 요일로 변환"""
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return WEEKDAY_KR[dt.weekday()]
    except Exception:
        pass
    return ""

def _format_date_with_weekday(date_str: str, time_str: str = None) -> str:
    """날짜를 요일 포함 형식으로 변환 (예: 12월 22일 월요일 오후 1시)"""
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_KR[dt.weekday()]
            date_formatted = f"{dt.month}월 {dt.day}일 {weekday}"
            
            if time_str:
                # 시간 변환 (HH:MM -> 오전/오후 X시)
                if re.match(r'^\d{1,2}:\d{2}$', time_str):
                    parts = time_str.split(':')
                    hour = int(parts[0])
                    if hour < 12:
                        time_formatted = f"오전 {hour}시" if hour > 0 else "오전 12시"
                    else:
                        time_formatted = f"오후 {hour - 12}시" if hour > 12 else "오후 12시"
                    return f"{date_formatted} {time_formatted}"
            return date_formatted
    except Exception:
        pass
    return f"{date_str} {time_str}" if time_str else date_str


class PersonalAgent:
    """
    개인 AI 에이전트
    - 자신의 캘린더만 접근
    - GPT를 사용한 협상 로직
    - 유연한 협상 스타일
    """
    
    def __init__(self, user_id: str, user_name: str):
        self.user_id = user_id
        self.user_name = user_name
        self.openai = OpenAIService()
        self.style = "flexible"  # 유연한 협상 스타일
        self._cached_availability: Optional[List[TimeSlot]] = None
    
    async def get_availability(
        self,
        date_range_start: datetime,
        date_range_end: datetime,
        duration_minutes: int = 60
    ) -> List[TimeSlot]:
        """
        내 캘린더에서 가용 시간 슬롯 조회
        """
        try:
            # 캘린더 토큰 확보
            access_token = await AuthService.get_valid_access_token_by_user_id(self.user_id)
            if not access_token:
                logger.warning(f"[{self.user_name}] 캘린더 토큰 없음")
                return []
            
            service = GoogleCalendarService()
            events = await service.get_calendar_events(
                access_token=access_token,
                time_min=date_range_start.isoformat(),
                time_max=date_range_end.isoformat()
            )
            
            # 바쁜 시간 추출
            busy_intervals = []
            for e in events:
                try:
                    start_str = e.start.get("dateTime")
                    end_str = e.end.get("dateTime")
                    if start_str and end_str:
                        start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        busy_intervals.append((start, end))
                except Exception:
                    continue
            
            # 병합
            busy_intervals.sort(key=lambda x: x[0])
            merged = []
            for s, e in busy_intervals:
                if not merged or s > merged[-1][1]:
                    merged.append([s, e])
                else:
                    merged[-1][1] = max(merged[-1][1], e)
            
            # 가용 시간 계산 (9시 ~ 22시 사이)
            available_slots = []
            current_date = date_range_start.date()
            end_date = date_range_end.date()
            
            while current_date <= end_date:
                day_start = datetime(
                    current_date.year, current_date.month, current_date.day,
                    0, 0, 0, tzinfo=KST
                )
                day_end = datetime(
                    current_date.year, current_date.month, current_date.day,
                    23, 59, 59, tzinfo=KST
                )
                
                # 해당 날짜의 바쁜 시간 필터링
                day_busy = [
                    (max(s, day_start), min(e, day_end))
                    for s, e in merged
                    if s < day_end and e > day_start
                ]
                day_busy.sort(key=lambda x: x[0])
                
                # 빈 슬롯 찾기
                cursor = day_start
                for busy_start, busy_end in day_busy:
                    if cursor < busy_start:
                        slot_duration = (busy_start - cursor).total_seconds() / 60
                        if slot_duration >= duration_minutes:
                            available_slots.append(TimeSlot(start=cursor, end=busy_start))
                    cursor = max(cursor, busy_end)
                
                # 마지막 슬롯
                if cursor < day_end:
                    slot_duration = (day_end - cursor).total_seconds() / 60
                    if slot_duration >= duration_minutes:
                        available_slots.append(TimeSlot(start=cursor, end=day_end))
                
                current_date += timedelta(days=1)
            
            self._cached_availability = available_slots
            logger.info(f"[{self.user_name}] 가용 슬롯 {len(available_slots)}개 발견")
            return available_slots
            
        except Exception as e:
            logger.error(f"[{self.user_name}] 가용 시간 조회 실패: {e}")
            return []
    
    async def evaluate_proposal(
        self,
        proposal: Proposal,
        context: Dict[str, Any]
    ) -> AgentDecision:
        """
        제안을 평가하고 GPT로 응답 결정
        ⚠️ 캘린더 충돌 시 GPT 호출 없이 강제 COUNTER
        """
        try:
            # 내 가용 시간 확인
            now = datetime.now(KST)
            availability = self._cached_availability or await self.get_availability(
                now, now + timedelta(days=14)
            )
            
            print(f"🔍 [DEBUG] [{self.user_name}] 가용 슬롯 수: {len(availability)}개")
            
            # 제안 시간이 내 가용 시간 안에 있는지 확인
            proposed_dt = self._parse_proposal_datetime(proposal)
            is_available = False
            
            print(f"🔍 [DEBUG] [{self.user_name}] 제안 시간: {proposal.date} {proposal.time} -> parsed: {proposed_dt}")
            
            if proposed_dt:
                for slot in availability:
                    if slot.start <= proposed_dt < slot.end:
                        is_available = True
                        print(f"✅ [DEBUG] [{self.user_name}] 제안 시간이 슬롯 내에 있음: {slot.start} ~ {slot.end}")
                        break
            
            print(f"🔍 [DEBUG] [{self.user_name}] is_available={is_available}, availability_count={len(availability)}")
            
            # 🚨 강제 차단: 캘린더 충돌 시 GPT 호출 없이 즉시 COUNTER
            if not is_available and availability:
                # 제안 시간과 가장 가까운 가용 슬롯 찾기
                best_slot = self._find_best_alternative_slot(proposed_dt, availability)
                
                if best_slot:
                    counter_proposal = Proposal(
                        date=best_slot.start.strftime("%Y-%m-%d"),
                        time=best_slot.start.strftime("%H:%M"),
                        location=proposal.location,
                        activity=proposal.activity,
                        duration_minutes=proposal.duration_minutes
                    )
                    
                    logger.info(f"[{self.user_name}] 🚫 캘린더 충돌! 강제 COUNTER - 제안: {proposal.date} {proposal.time} → 역제안: {counter_proposal.date} {counter_proposal.time}")
                    
                    # 정확한 요일 포함 날짜 형식
                    original_formatted = _format_date_with_weekday(proposal.date, proposal.time)
                    counter_formatted = _format_date_with_weekday(counter_proposal.date, counter_proposal.time)
                    
                    # 메시지만 LLM으로 생성 (팩트 주입 - 정확한 요일 포함)
                    try:
                        counter_message = await self.openai.generate_a2a_message(
                            agent_name=f"{self.user_name}의 비서",
                            receiver_name=context.get("other_names", "상대방"),
                            context=f"일정 충돌로 대안 시간을 제안합니다. '{counter_formatted}'을 정중하게 제안하는 메시지를 작성하세요.",
                            tone="friendly_counter"
                        )
                    except Exception as e:
                        logger.warning(f"[{self.user_name}] 메시지 생성 실패, 기본 메시지 사용: {e}")
                        counter_message = f"그 시간은 일정이 있어요 😅 {counter_formatted}은 어떠세요?"
                    
                    return AgentDecision(
                        action=MessageType.COUNTER,
                        proposal=counter_proposal,
                        reason="캘린더 충돌 - 팩트 기반 역제안",
                        message=counter_message
                    )
            
            # 가용 시간이 전혀 없는 경우
            if not is_available and not availability:
                logger.warning(f"[{self.user_name}] 2주 내 가용 시간 없음")
                return AgentDecision(
                    action=MessageType.NEED_HUMAN,
                    message="앗, 2주 내에 가능한 시간이 없어요 😥 직접 확인해주세요!",
                    reason="no_availability"
                )
            
            # ✅ 혼합 방식: 결정은 코드, 메시지는 LLM
            # 캘린더 상태가 명확하므로 코드에서 즉시 결정
            
            if is_available:
                # ============================================
                # 🎯 캘린더 가용 → 강제 ACCEPT (LLM 결정 X)
                # ============================================
                logger.info(f"[{self.user_name}] ✅ 캘린더 가용! 강제 ACCEPT - {proposal.date} {proposal.time}")
                
                # 정확한 요일 포함 날짜 형식
                formatted_datetime = _format_date_with_weekday(proposal.date, proposal.time)
                
                # 메시지만 LLM으로 생성 (팩트 주입 - 정확한 요일 포함)
                try:
                    accept_message = await self.openai.generate_a2a_message(
                        agent_name=f"{self.user_name}의 비서",
                        receiver_name=context.get("other_names", "상대방"),
                        context=f"상대방이 '{formatted_datetime}'에 만나자고 제안했고 캘린더가 비어있어서 수락합니다. '좋아요, {formatted_datetime}에 뵙겠습니다!' 처럼 흔쾌히 동의하는 메시지를 작성하세요.",
                        tone="friendly_accept"
                    )
                except Exception as e:
                    logger.warning(f"[{self.user_name}] 메시지 생성 실패, 기본 메시지 사용: {e}")
                    accept_message = f"좋아요! {formatted_datetime}에 뵐게요 😊"
                
                return AgentDecision(
                    action=MessageType.ACCEPT,
                    proposal=proposal,
                    reason="캘린더 가용 - 팩트 기반 수락",
                    message=accept_message
                )
            
            else:
                # ============================================
                # 🚫 캘린더 충돌 → 강제 COUNTER (LLM 결정 X)
                # ============================================
                # 이 케이스는 위에서 이미 처리됨 (lines 163-184)
                # 여기 도달하면 availability가 비어있는 경우
                logger.warning(f"[{self.user_name}] 예상치 못한 상태 - is_available=False, availability={len(availability)}")
                
                # 첫 번째 가용 슬롯으로 역제안
                if availability:
                    best_slot = availability[0]
                    counter_proposal = Proposal(
                        date=best_slot.start.strftime("%Y-%m-%d"),
                        time=best_slot.start.strftime("%H:%M"),
                        location=proposal.location,
                        activity=proposal.activity,
                        duration_minutes=proposal.duration_minutes
                    )
                    
                    # 메시지만 LLM으로 생성 (팩트 주입)
                    try:
                        counter_message = await self.openai.generate_a2a_message(
                            agent_name=f"{self.user_name}의 비서",
                            receiver_name=context.get("other_names", "상대방"),
                            context=f"일정 충돌로 대안 시간을 제안합니다. '{counter_proposal.date} {counter_proposal.time}'을 정중하게 제안하는 메시지를 작성하세요.",
                            tone="friendly_counter"
                        )
                    except Exception as e:
                        logger.warning(f"[{self.user_name}] 메시지 생성 실패, 기본 메시지 사용: {e}")
                        counter_message = f"그 시간은 일정이 있어요 😅 {best_slot.start.strftime('%m/%d %H:%M')} 어때요?"
                    
                    return AgentDecision(
                        action=MessageType.COUNTER,
                        proposal=counter_proposal,
                        reason="캘린더 충돌 - 팩트 기반 역제안",
                        message=counter_message
                    )
                else:
                    return AgentDecision(
                        action=MessageType.NEED_HUMAN,
                        message="가능한 시간을 찾지 못했어요 😅",
                        reason="no_available_slot"
                    )
            
        except Exception as e:
            logger.error(f"[{self.user_name}] 제안 평가 실패: {e}")
            print(f"❌ [ERROR] [{self.user_name}] evaluate_proposal 예외 발생: {e}")
            # 오류 발생 시 사람에게 넘김 (자동 ACCEPT 하지 않음!)
            return AgentDecision(
                action=MessageType.NEED_HUMAN,
                message="오류가 발생했어요. 직접 확인해주세요. 😅",
                reason=f"error: {str(e)}"
            )
    
    async def make_initial_proposal(
        self,
        target_date: Optional[str],
        target_time: Optional[str],
        activity: Optional[str],
        location: Optional[str],
        context: Dict[str, Any]
    ) -> AgentDecision:
        """
        초기 제안 생성
        ⚠️ 사용자가 지정한 시간도 자신의 캘린더와 충돌하는지 확인!
        """
        try:
            now = datetime.now(KST)
            availability = await self.get_availability(
                now, now + timedelta(days=14)
            )
            
            print(f"🔍 [DEBUG] [{self.user_name}] make_initial_proposal - 가용 슬롯 수: {len(availability)}개")
            
            if not availability:
                return AgentDecision(
                    action=MessageType.NEED_HUMAN,
                    message="앗, 2주 내 가용 시간이 없어요 😅",
                    reason="no_availability"
                )
            
            # 상대 날짜/시간을 실제 날짜/시간으로 변환
            actual_date = self._convert_relative_date(target_date, now) if target_date else None
            actual_time = self._convert_relative_time(target_time, activity) if target_time else None
            
            logger.info(f"[{self.user_name}] 초기 제안 - 원본: {target_date} {target_time} → 변환: {actual_date} {actual_time}")
            
            proposal = None
            
            # 사용자가 지정한 날짜/시간이 있으면 먼저 확인
            time_was_changed = False  # 시간이 변경되었는지 추적
            original_time = actual_time  # 원래 요청 시간 저장
            
            if actual_date and actual_time:
                # 지정 시간이 내 가용 시간 안에 있는지 확인
                target_dt = self._parse_datetime(actual_date, actual_time)
                is_available = False
                
                if target_dt:
                    for slot in availability:
                        if slot.start <= target_dt < slot.end:
                            is_available = True
                            print(f"✅ [DEBUG] [{self.user_name}] 지정 시간 {target_dt}가 가용 슬롯 내에 있음")
                            break
                
                if is_available:
                    # 지정 시간이 가용 시간 내면 사용
                    proposal = Proposal(
                        date=actual_date,
                        time=actual_time,
                        activity=activity,
                        location=location
                    )
                else:
                    # 지정 시간이 충돌! → 가장 가까운 가용 슬롯 찾기
                    print(f"🚫 [DEBUG] [{self.user_name}] 지정 시간 {actual_date} {actual_time}이 캘린더 충돌! 대안 찾는 중...")
                    best_slot = self._find_best_alternative_slot(target_dt, availability)
                    if best_slot:
                        time_was_changed = True  # 시간이 변경됨
                        proposal = Proposal(
                            date=best_slot.start.strftime("%Y-%m-%d"),
                            time=best_slot.start.strftime("%H:%M"),
                            activity=activity,
                            location=location
                        )
                        print(f"✅ [DEBUG] [{self.user_name}] 대안 시간 제안: {proposal.date} {proposal.time}")
            
            # 사용자 지정 시간이 없거나 proposal이 아직 없으면 시간 선호도에 맞는 슬롯 찾기
            if not proposal:
                # 시간 선호도가 있으면 해당 시간대 슬롯 우선 탐색
                if actual_time:
                    preferred_hour = int(actual_time.split(":")[0]) if ":" in actual_time else 18
                    
                    # 선호 시간대(±2시간)에 맞는 슬롯 찾기
                    matching_slots = []
                    for slot in availability:
                        slot_hour = slot.start.hour
                        if abs(slot_hour - preferred_hour) <= 2:
                            matching_slots.append(slot)
                    
                    if matching_slots:
                        best_slot = matching_slots[0]
                        logger.info(f"[{self.user_name}] 시간 선호도 {actual_time}에 맞는 슬롯 발견: {best_slot.start}")
                    else:
                        # 선호 시간대에 맞는 슬롯이 없으면 첫 번째 슬롯 사용
                        best_slot = availability[0]
                        logger.info(f"[{self.user_name}] 시간 선호도 {actual_time}에 맞는 슬롯 없음, 첫 번째 슬롯 사용: {best_slot.start}")
                else:
                    best_slot = availability[0]
                
                proposal = Proposal(
                    date=best_slot.start.strftime("%Y-%m-%d"),
                    time=best_slot.start.strftime("%H:%M"),
                    activity=activity,
                    location=location
                )
            
            # 메시지 생성 - LLM에 팩트 주입 (정확한 요일 포함)
            proposal_formatted = _format_date_with_weekday(proposal.date, proposal.time)
            
            try:
                if time_was_changed:
                    # 시간이 변경된 경우 - 원래 시간은 안 되고 대안 제시
                    message = await self.openai.generate_a2a_message(
                        agent_name=f"{self.user_name}의 비서",
                        receiver_name=context.get("other_names", "상대방"),
                        context=f"캘린더 충돌로 대체 시간을 제안합니다. '{proposal_formatted}'을 정중하게 제안하는 메시지를 작성하세요.",
                        tone="friendly_alternative"
                    )
                else:
                    # 시간 변경 없음 - 흔쾌히 초대
                    message = await self.openai.generate_a2a_message(
                        agent_name=f"{self.user_name}의 비서",
                        receiver_name=context.get("other_names", "상대방"),
                        context=f"'{proposal_formatted}'에 {activity or '약속'}을 제안합니다. '어떠세요?' 형식으로 자연스럽게 제안하는 메시지를 작성하세요.",
                        tone="friendly_propose"
                    )
            except Exception as e:
                logger.warning(f"[{self.user_name}] 메시지 생성 실패, 기본 메시지 사용: {e}")
                if time_was_changed:
                    message = f"그 시간은 제 일정이 있어서 {proposal_formatted}에 제안드려요! 😊"
                else:
                    message = f"{proposal_formatted}에 {activity or '약속'} 어떠세요? 😊"
            
            return AgentDecision(
                action=MessageType.PROPOSE,
                proposal=proposal,
                message=message
            )
            
        except Exception as e:
            logger.error(f"[{self.user_name}] 초기 제안 생성 실패: {e}")
            return AgentDecision(
                action=MessageType.NEED_HUMAN,
                message="제안 생성 중 오류가 발생했어요 😥"
            )
    
    def _find_best_alternative_slot(self, proposed_dt: Optional[datetime], availability: List[TimeSlot]) -> Optional[TimeSlot]:
        """
        제안 시간과 가장 가까운 가용 슬롯 찾기
        - 같은 날짜 슬롯 우선
        - 없으면 시간 차이가 가장 작은 슬롯
        """
        if not availability:
            return None
        
        if not proposed_dt:
            # 제안 시간을 파싱할 수 없으면 첫 번째 가용 슬롯 반환
            return availability[0]
        
        # 같은 날짜의 슬롯 찾기
        same_day_slots = [
            slot for slot in availability 
            if slot.start.date() == proposed_dt.date()
        ]
        
        if same_day_slots:
            # 같은 날짜 중 제안 시간과 가장 가까운 슬롯
            return min(same_day_slots, key=lambda s: abs((s.start - proposed_dt).total_seconds()))
        
        # 같은 날짜 슬롯이 없으면 전체에서 가장 가까운 슬롯
        return min(availability, key=lambda s: abs((s.start - proposed_dt).total_seconds()))
    
    def _parse_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """날짜와 시간 문자열을 datetime으로 변환"""
        import re
        try:
            # 날짜 파싱
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                return None
            
            # 시간 파싱 (HH:MM 형식)
            if re.match(r'^\d{1,2}:\d{2}$', time_str):
                parts = time_str.split(':')
                hour, minute = int(parts[0]), int(parts[1])
            else:
                return None
            
            return datetime(parsed_date.year, parsed_date.month, parsed_date.day, 
                           hour, minute, tzinfo=KST)
        except Exception as e:
            logger.error(f"_parse_datetime 실패: {e}")
            return None
    
    def _convert_relative_date(self, date_str: str, now: datetime) -> Optional[str]:
        """상대 날짜를 YYYY-MM-DD 형식으로 변환"""
        import re
        
        if not date_str:
            return None
        
        # 이미 YYYY-MM-DD 형식이면 그대로 반환
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str
        
        # 요일 처리 (월요일~일요일)
        weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        target_weekday = None
        for i, w in enumerate(weekdays):
            if w in date_str:
                target_weekday = i
                break
        
        if target_weekday is not None:
            # 요일 발견
            current_weekday = now.weekday()
            
            if "다음주" in date_str or "다음 주" in date_str:
                # 다음주 X요일 = 다음 주 월요일 + X일
                days_to_next_monday = (7 - current_weekday) % 7
                if days_to_next_monday == 0:
                    days_to_next_monday = 7
                days_ahead = days_to_next_monday + target_weekday
            else:
                # 이번주 X요일
                days_ahead = (target_weekday - current_weekday) % 7
                if days_ahead == 0:
                    # 오늘이 해당 요일이면 그대로 (또는 다음 주로 할 수도 있음)
                    pass
            
            target_date = (now + timedelta(days=days_ahead)).date()
            return target_date.strftime("%Y-%m-%d")

        # 상대 날짜 변환
        if "오늘" in date_str:
            target_date = now.date()
        elif "내일" in date_str:
            target_date = (now + timedelta(days=1)).date()
        elif "모레" in date_str:
            target_date = (now + timedelta(days=2)).date()
        elif "다음주" in date_str or "다음 주" in date_str:
            # 다음주 월요일 기준 (요일 지정 없는 경우)
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            target_date = (now + timedelta(days=days_until_monday)).date()
        elif "이번주" in date_str or "이번 주" in date_str:
            target_date = now.date()
        else:
            # "12월 12일" 형식
            match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', date_str)
            if match:
                month = int(match.group(1))
                day = int(match.group(2))
                year = now.year
                # 이미 지난 날짜면 내년으로
                if month < now.month or (month == now.month and day < now.day):
                    year += 1
                try:
                    target_date = datetime(year, month, day).date()
                except ValueError:
                    return None
            else:
                # "13일" 형식 (월 없이 일만 있는 경우) - 현재 월 기준
                match_day_only = re.search(r'(\d{1,2})일', date_str)
                if match_day_only:
                    day = int(match_day_only.group(1))
                    month = now.month
                    year = now.year
                    # 이미 지난 날짜면 다음 달로
                    if day < now.day:
                        month += 1
                        if month > 12:
                            month = 1
                            year += 1
                    try:
                        target_date = datetime(year, month, day).date()
                    except ValueError:
                        return None
                else:
                    return None
        
        return target_date.strftime("%Y-%m-%d")
    
    def _convert_relative_time(self, time_str: str, activity: Optional[str] = None) -> Optional[str]:
        """
        상대 시간을 HH:MM 형식으로 변환
        오전/오후가 명시되지 않은 경우 활동에 따라 추론
        """
        import re
        
        if not time_str:
            return None
        
        # 이미 HH:MM 형식이면 그대로 반환
        if re.match(r'^\d{1,2}:\d{2}$', time_str):
            return time_str
        
        # 한국어 시간 파싱
        hour = None
        minute = 0
        
        # "오후 3시", "오전 10시 30분" 등
        hour_match = re.search(r'(\d{1,2})\s*시', time_str)
        if hour_match:
            hour = int(hour_match.group(1))
            
            # 오후/오전 처리
            if "오후" in time_str and hour < 12:
                hour += 12
            elif "오전" in time_str and hour == 12:
                hour = 0
            elif "오전" not in time_str and "오후" not in time_str:
                # 오전/오후 명시 안 됨 → 활동 기반 추론
                hour = self._infer_am_pm(hour, time_str, activity)
            
            # 분 처리
            min_match = re.search(r'(\d{1,2})\s*분', time_str)
            if min_match:
                minute = int(min_match.group(1))
        
        if hour is not None:
            return f"{hour:02d}:{minute:02d}"
        
        # "점심", "저녁" 등 대략적인 시간
        if "점심" in time_str:
            return "12:00"
        elif "저녁" in time_str:
            return "18:00"
        elif "아침" in time_str:
            return "09:00"
        
        return None
    
    def _infer_am_pm(self, hour: int, time_str: str, activity: Optional[str] = None) -> int:
        """
        오전/오후가 명시되지 않은 경우 추론
        - 1~6시: 대부분 오후 (13:00~18:00)
        - 7~11시: 활동에 따라 판단
        - 12시: 그대로
        """
        # 밤 키워드 체크
        if "밤" in time_str or "저녁" in time_str:
            if hour < 12:
                return hour + 12
            return hour
        
        # 1~6시는 대부분 오후
        if 1 <= hour <= 6:
            return hour + 12
        
        # 7~11시는 활동에 따라 판단
        if 7 <= hour <= 11:
            # 업무/미팅 관련은 오전일 가능성
            morning_keywords = ["아침", "조찬", "모닝"]
            # 저녁 활동 관련은 오후일 가능성
            evening_keywords = ["저녁", "술", "회식", "밥", "디너", "dinner"]
            
            if activity:
                activity_lower = activity.lower()
                for keyword in evening_keywords:
                    if keyword in activity_lower or keyword in time_str:
                        return hour + 12
                for keyword in morning_keywords:
                    if keyword in activity_lower or keyword in time_str:
                        return hour  # 오전 유지
            
            # 기본값: 오전으로 유지 (업무 미팅 가정)
            return hour
        
        return hour
    
    def _parse_proposal_datetime(self, proposal: Proposal) -> Optional[datetime]:
        """제안의 날짜/시간을 datetime으로 변환"""
        import re
        try:
            date_str = proposal.date
            time_str = proposal.time
            
            # 현재 연도
            current_year = datetime.now(KST).year
            
            # 날짜 파싱 시도 (여러 형식 지원)
            parsed_date = None
            
            # 1. YYYY-MM-DD 형식
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            # 2. MM-DD 형식 (연도 없음)
            elif re.match(r'^\d{2}-\d{2}$', date_str):
                parsed_date = datetime.strptime(f"{current_year}-{date_str}", "%Y-%m-%d").date()
            # 3. 한국어 형식 "12월 12일"
            elif "월" in date_str and "일" in date_str:
                match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', date_str)
                if match:
                    month = int(match.group(1))
                    day = int(match.group(2))
                    parsed_date = datetime(current_year, month, day).date()
            
            if not parsed_date:
                return None
            
            # 시간 파싱 (HH:MM 또는 한국어)
            parsed_time = None
            
            # 1. HH:MM 형식
            if re.match(r'^\d{1,2}:\d{2}$', time_str):
                parts = time_str.split(':')
                parsed_time = (int(parts[0]), int(parts[1]))
            # 2. 한국어 형식 "오후 3시", "오전 10시"
            elif "시" in time_str:
                match = re.search(r'(\d{1,2})\s*시', time_str)
                if match:
                    hour = int(match.group(1))
                    if "오후" in time_str and hour < 12:
                        hour += 12
                    elif "오전" in time_str and hour == 12:
                        hour = 0
                    parsed_time = (hour, 0)
            
            if not parsed_time:
                return None
            
            dt = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 
                         parsed_time[0], parsed_time[1], tzinfo=KST)
            return dt
            
        except Exception as e:
            logger.error(f"날짜 파싱 실패: {e}")
            return None
