"""
NegotiationEngine - 다중 참여자 협상 엔진
"""
import logging
import uuid
import asyncio
import json
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from .a2a_protocol import (
    MessageType, Proposal, A2AMessage, AgentDecision,
    NegotiationStatus, NegotiationResult, HumanInterventionReason, TimeSlot,
    ConflictInfo, ParticipantAvailability, MajorityRecommendation
)
from .personal_agent import PersonalAgent
from .a2a_repository import A2ARepository
from src.auth.auth_repository import AuthRepository
from src.chat.chat_repository import ChatRepository

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


@dataclass
class RecommendedSlot:
    """추천 슬롯 정보"""
    date: str  # "2025-12-17"
    time_condition: Optional[str] = None  # "6시 이후", "2시 이전", None (종일)
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    available_users: List[str] = field(default_factory=list)
    unavailable_users: List[str] = field(default_factory=list)
    is_all_available: bool = False
    priority_score: int = 0  # 높을수록 좋음


@dataclass
class DateRecommendation:
    """날짜 추천 결과"""
    date: str
    condition: str
    display_text: str  # "12/17 (6시 이후) - 3명 가능"
    available_count: int
    unavailable_names: List[str] = field(default_factory=list)


def _clean_llm_message(message: str) -> str:
    """LLM 응답에서 JSON이 섞여있으면 자연스러운 텍스트만 추출"""
    if not message:
        return message
    
    message = message.strip()
    
    # JSON 형식인지 확인 (다양한 필드 처리)
    if message.startswith("{"):
        try:
            # 1. 완벽한 JSON인 경우
            parsed = json.loads(message)
            if isinstance(parsed, dict):
                # message 필드 우선
                if "message" in parsed:
                    extracted = parsed.get("message", "")
                    if extracted:
                        logger.info(f"[LLM Cleanup] JSON.message → Text: {extracted[:30]}...")
                        return extracted.strip('"').strip("'")
                
                # reason 필드 (message가 없을 때)
                if "reason" in parsed:
                    extracted = parsed.get("reason", "")
                    if extracted and not extracted.startswith("{"):
                        logger.info(f"[LLM Cleanup] JSON.reason → Text: {extracted[:30]}...")
                        return extracted.strip('"').strip("'")
            
        except json.JSONDecodeError:
            # 2. JSON + 텍스트 혼합된 경우 (예: {"action": "accept"} 좋아요!)
            # 앞부분의 JSON 객체 패턴 제거
            import re
            json_match = re.match(r'^(\{.*?\})\s*(.*)', message, re.DOTALL)
            if json_match:
                json_part = json_match.group(1)
                text_part = json_match.group(2)
                if text_part.strip():
                    logger.info(f"[LLM Cleanup] Mixed JSON removed. Keeping text: {text_part[:30]}...")
                    return text_part.strip().strip('"').strip("'")
            pass
    
    # 따옴표 제거
    message = message.strip('"').strip("'")
    
    return message


class NegotiationEngine:
    """
    다중 참여자 협상 엔진
    - 최대 5라운드 관리
    - 실시간 SSE 스트리밍
    - 합의 판정 (전원 동의 시 확정)
    - 사용자 개입 조건 판단
    """
    
    MAX_ROUNDS = 5
    
    def __init__(
        self,
        session_id: str,
        initiator_user_id: str,
        participant_user_ids: List[str],
        activity: Optional[str] = None,
        location: Optional[str] = None,
        target_date: Optional[str] = None,
        target_time: Optional[str] = None
    ):
        self.session_id = session_id
        self.initiator_user_id = initiator_user_id
        self.participant_user_ids = participant_user_ids
        self.activity = activity
        self.location = location
        self.target_date = target_date
        self.target_time = target_time
        
        self.agents: Dict[str, PersonalAgent] = {}
        self.current_round = 0
        self.status = NegotiationStatus.IN_PROGRESS
        self.messages: List[A2AMessage] = []
        self.last_proposals: Dict[str, Proposal] = {}  # 교착 상태 탐지용
        self.deadlock_counter = 0
        self.user_names: Dict[str, str] = {}  # user_id -> user_name 매핑
        self.awaiting_choice_from: List[str] = []  # 충돌 선택 대기 중인 사용자 리스트
    
    async def initialize_agents(self):
        """모든 참여자의 에이전트 초기화"""
        all_user_ids = [self.initiator_user_id] + self.participant_user_ids
        
        for user_id in all_user_ids:
            user = await AuthRepository.find_user_by_id(user_id)
            user_name = user.get("name", "사용자") if user else "사용자"
            self.agents[user_id] = PersonalAgent(user_id, user_name)
            self.user_names[user_id] = user_name
            logger.info(f"에이전트 초기화: {user_name}")
    
    async def collect_all_availabilities(
        self, 
        start: datetime, 
        end: datetime
    ) -> Dict[str, List[TimeSlot]]:
        """모든 참여자의 가용 시간을 수집"""
        await self.initialize_agents()
        
        results = {}
        all_user_ids = [self.initiator_user_id] + self.participant_user_ids
        
        for user_id in all_user_ids:
            agent = self.agents[user_id]
            slots = await agent.get_availability(start, end)
            results[user_id] = slots
            logger.info(f"[{self.user_names.get(user_id, '사용자')}] 가용 슬롯 {len(slots)}개 수집")
        
        return results
    
    async def analyze_participant_availability(
        self,
        target_dt: datetime,
        proposal: Proposal
    ) -> Tuple[List[ParticipantAvailability], bool]:
        """
        특정 시간에 대한 모든 참여자의 가용성 분석
        Returns: (참여자별 가용성 리스트, 전원 가능 여부)
        """
        all_user_ids = [self.initiator_user_id] + self.participant_user_ids
        total_count = len(all_user_ids)
        results: List[ParticipantAvailability] = []
        all_available = True
        
        for user_id in all_user_ids:
            agent = self.agents.get(user_id)
            if not agent:
                continue
            
            user_name = self.user_names.get(user_id, "사용자")
            
            # 가용성 확인
            availability = agent._cached_availability or await agent.get_availability(
                datetime.now(KST), datetime.now(KST) + timedelta(days=14)
            )
            
            is_available = False
            if target_dt:
                for slot in availability:
                    if slot.start <= target_dt < slot.end:
                        is_available = True
                        break
            
            conflict_info = None
            if not is_available:
                all_available = False
                # 충돌 일정 정보 가져오기
                conflict_info = agent.find_conflicting_event(target_dt)
            
            results.append(ParticipantAvailability(
                user_id=user_id,
                user_name=user_name,
                is_available=is_available,
                conflict_info=conflict_info,
                choice=None
            ))
        
        logger.info(f"참여자 가용성 분석: {len([r for r in results if r.is_available])}/{total_count}명 가능")
        return results, all_available
    
    def get_majority_recommendations(
        self,
        availabilities: Dict[str, List[TimeSlot]],
        max_count: int = 3
    ) -> List[MajorityRecommendation]:
        """
        과반수 가능 날짜 추천 (전원 가능 날짜가 없을 때 사용)
        """
        recommendations = self.find_intersection_slots(availabilities)
        
        # 전원 가능한 날짜가 있는지 확인
        all_available_dates = [r for r in recommendations if r.is_all_available]
        if all_available_dates:
            # 전원 가능 있으면 과반수 추천 필요 없음
            return []
        
        # 과반수 이상 가능한 날짜 필터링
        total_users = len(availabilities)
        majority_threshold = total_users // 2 + 1  # 과반수 기준
        
        majority_recs = [
            r for r in recommendations 
            if len(r.available_users) >= majority_threshold
        ]
        
        results: List[MajorityRecommendation] = []
        for rec in majority_recs[:max_count]:
            dt = datetime.strptime(rec.date, "%Y-%m-%d")
            date_display = f"{dt.month}월 {dt.day}일"
            
            results.append(MajorityRecommendation(
                date=date_display,
                time_condition=rec.time_condition or "시간 무관",
                available_count=len(rec.available_users),
                total_count=total_users,
                available_names=rec.available_users,
                unavailable_names=rec.unavailable_users,
                is_majority=len(rec.available_users) >= majority_threshold
            ))
        
        logger.info(f"과반수 추천: {len(results)}개 (기준: {majority_threshold}명 이상)")
        return results
    
    def find_intersection_slots(
        self, 
        availabilities: Dict[str, List[TimeSlot]],
        preferred_hour: Optional[int] = None
    ) -> List[RecommendedSlot]:
        """교집합 계산 및 우선순위 정렬"""
        all_user_ids = list(availabilities.keys())
        total_users = len(all_user_ids)
        
        # 날짜별로 가용 시간 그룹화
        date_slots: Dict[str, Dict[str, List[TimeSlot]]] = {}
        
        for user_id, slots in availabilities.items():
            for slot in slots:
                date_str = slot.start.strftime("%Y-%m-%d")
                if date_str not in date_slots:
                    date_slots[date_str] = {}
                if user_id not in date_slots[date_str]:
                    date_slots[date_str][user_id] = []
                date_slots[date_str][user_id].append(slot)
        
        recommendations = []
        
        for date_str, user_slots in date_slots.items():
            available_users = list(user_slots.keys())
            unavailable_users = [uid for uid in all_user_ids if uid not in available_users]
            
            # 시간대 분석
            # 모든 사용자의 슬롯 교집합 시간대 찾기
            common_hours = set(range(9, 22))  # 9시~22시 기본
            
            for user_id in available_users:
                user_hours = set()
                for slot in user_slots[user_id]:
                    for hour in range(slot.start.hour, min(slot.end.hour + 1, 22)):
                        user_hours.add(hour)
                common_hours = common_hours.intersection(user_hours)
            
            # 시간 조건 결정
            time_condition = None
            start_hour = None
            end_hour = None
            
            if common_hours:
                min_hour = min(common_hours)
                max_hour = max(common_hours)
                
                if min_hour >= 18:
                    time_condition = f"{min_hour}시 이후"
                    start_hour = min_hour
                elif max_hour <= 14:
                    time_condition = f"{max_hour}시 이전"
                    end_hour = max_hour
                elif len(common_hours) == 13:  # 9~21시 전체
                    time_condition = "시간 무관"
                else:
                    time_condition = f"{min_hour}시~{max_hour}시"
                    start_hour = min_hour
                    end_hour = max_hour
            
            # 우선순위 계산
            priority = len(available_users) * 10
            if len(available_users) == total_users:
                priority += 100  # 전원 가능 보너스
            if preferred_hour and common_hours and preferred_hour in common_hours:
                priority += 20  # 선호 시간대 보너스
            
            rec = RecommendedSlot(
                date=date_str,
                time_condition=time_condition,
                start_hour=start_hour,
                end_hour=end_hour,
                available_users=[self.user_names.get(uid, uid) for uid in available_users],
                unavailable_users=[self.user_names.get(uid, uid) for uid in unavailable_users],
                is_all_available=(len(available_users) == total_users),
                priority_score=priority
            )
            recommendations.append(rec)
        
        # 우선순위 내림차순 정렬
        recommendations.sort(key=lambda x: x.priority_score, reverse=True)
        
        return recommendations
    
    def recommend_best_dates(self, recommendations: List[RecommendedSlot], max_count: int = 3) -> List[DateRecommendation]:
        """상위 N개 날짜 추천 + 조건 설명"""
        results = []
        
        for rec in recommendations[:max_count]:
            # 날짜 포맷팅 (12/17)
            dt = datetime.strptime(rec.date, "%Y-%m-%d")
            date_display = f"{dt.month}/{dt.day}"
            
            # 가능 인원 표시
            available_count = len(rec.available_users)
            
            if rec.is_all_available:
                display = f"{date_display} ({rec.time_condition or '시간 무관'}) - 전원 가능"
            else:
                unavailable_str = ", ".join(rec.unavailable_users)
                display = f"{date_display} ({rec.time_condition or '시간 무관'}) - {available_count}명 가능 ({unavailable_str}님 제외)"
            
            results.append(DateRecommendation(
                date=rec.date,
                condition=rec.time_condition or "시간 무관",
                display_text=display,
                available_count=available_count,
                unavailable_names=rec.unavailable_users
            ))
        
        return results
    
    async def run_negotiation(self) -> AsyncGenerator[A2AMessage, None]:
        """
        협상 실행 (실시간 스트리밍)
        각 메시지마다 yield하여 SSE로 전송
        """
        await self.initialize_agents()
        
        initiator_agent = self.agents[self.initiator_user_id]
        other_names = ", ".join([
            self.agents[uid].user_name for uid in self.participant_user_ids
        ])
        
        # 초기 제안 생성
        self.current_round = 1
        initial_decision = await initiator_agent.make_initial_proposal(
            target_date=self.target_date,
            target_time=self.target_time,
            activity=self.activity,
            location=self.location,
            context={
                "other_names": other_names,
                "participant_count": len(self.participant_user_ids) + 1
            }
        )
        
        # 에이전트 가용시간 없음 → 사용자 개입
        if initial_decision.action == MessageType.NEED_HUMAN:
            msg = self._create_message(
                msg_type=MessageType.NEED_HUMAN,
                sender_id=self.initiator_user_id,
                message=initial_decision.message
            )
            yield msg
            self.status = NegotiationStatus.NEED_HUMAN
            return
        
        current_proposal = initial_decision.proposal
        
        # 초기 제안 메시지
        propose_msg = self._create_message(
            msg_type=MessageType.PROPOSE,
            sender_id=self.initiator_user_id,
            proposal=current_proposal,
            message=initial_decision.message
        )
        yield propose_msg
        await self._save_message(propose_msg)
        await asyncio.sleep(0.5)  # 실시간 효과
        
        # 협상 루프
        while self.current_round <= self.MAX_ROUNDS:
            all_accepted = True
            counter_proposals = []
            
            # 각 참여자에게 제안 평가 요청
            for participant_id in self.participant_user_ids:
                agent = self.agents[participant_id]
                
                # 확인 중 메시지
                checking_msg = self._create_message(
                    msg_type=MessageType.INFO,
                    sender_id=participant_id,
                    message=f"🔍 확인 중..."
                )
                yield checking_msg
                await asyncio.sleep(0.3)
                
                # 제안 평가
                decision = await agent.evaluate_proposal(
                    proposal=current_proposal,
                    context={
                        "round": self.current_round,
                        "participant_count": len(self.participant_user_ids) + 1
                    }
                )
                
                response_msg = self._create_message(
                    msg_type=decision.action,
                    sender_id=participant_id,
                    proposal=decision.proposal,
                    message=decision.message
                )
                yield response_msg
                await self._save_message(response_msg)
                await asyncio.sleep(0.5)
                
                if decision.action == MessageType.ACCEPT:
                    continue
                elif decision.action == MessageType.COUNTER:
                    # 충돌 정보가 있으면 사용자 선택 대기
                    if decision.conflict_info:
                        all_accepted = False
                        
                        # 충돌 선택지 메시지 생성
                        conflict_choice_msg = self._create_message(
                            msg_type=MessageType.CONFLICT_CHOICE,
                            sender_id=participant_id,
                            proposal=current_proposal,
                            message=f"{self.user_names.get(participant_id, '사용자')}님은 그 시간에 [{decision.conflict_info.event_name}]이 있습니다. 참석 불가 또는 일정 조정을 선택해주세요."
                        )
                        # 충돌 정보 추가
                        conflict_choice_msg.conflict_info = {
                            "event_name": decision.conflict_info.event_name,
                            "event_time_display": decision.conflict_info.event_time_display,
                            "user_id": participant_id,
                            "user_name": self.user_names.get(participant_id, "사용자")
                        }
                        yield conflict_choice_msg
                        await self._save_message(conflict_choice_msg)
                        
                        # 📢 충돌 사용자의 ChatScreen에 알림 메시지 저장
                        try:
                            initiator_name = self.user_names.get(self.initiator_user_id, "사용자")
                            participant_name = self.user_names.get(participant_id, "사용자")
                            
                            # 충돌 알림 메시지 JSON
                            chat_notification = {
                                "type": "schedule_conflict_choice",
                                "session_id": self.session_id,
                                "initiator_name": initiator_name,
                                "other_count": len(self.participant_user_ids),
                                "proposed_date": current_proposal.date,
                                "proposed_time": current_proposal.time,
                                "conflict_event_name": decision.conflict_info.event_name,
                                "text": f"🔔 {initiator_name}님이 {current_proposal.date} {current_proposal.time}에 일정을 잡으려 합니다. 그 시간에 [{decision.conflict_info.event_name}]이 있으시네요.",
                                "choices": [
                                    {"id": "skip", "label": "참석 불가"},
                                    {"id": "adjust", "label": "일정 조정 가능"}
                                ]
                            }
                            
                            # 참여자의 기본 채팅 세션에 알림 저장
                            default_session = await ChatRepository.get_default_session(participant_id)
                            if default_session:
                                await ChatRepository.add_message(
                                    session_id=default_session["id"],
                                    user_message=None,
                                    ai_response=json.dumps(chat_notification, ensure_ascii=False),
                                    intent="a2a_conflict_notification"
                                )
                                logger.info(f"[협상] 충돌 알림을 {participant_name}의 ChatScreen에 저장")
                        except Exception as chat_err:
                            logger.warning(f"[협상] 채팅 알림 저장 실패: {chat_err}")
                        
                        # 사용자 선택 대기 상태로 전환
                        self.status = NegotiationStatus.AWAITING_USER_CHOICE
                        self.awaiting_choice_from = [participant_id]
                        
                        # 협상 일시 중단 - 사용자 응답 후 재개
                        logger.info(f"[협상] 충돌 감지 - {participant_id} 사용자 선택 대기")
                        
                        # 세션 상태 업데이트
                        await A2ARepository.update_session_status(
                            self.session_id,
                            "awaiting_user_choice",
                            details={
                                "awaiting_from": participant_id,
                                "conflict_event": decision.conflict_info.event_name,
                                "proposed_date": current_proposal.date,
                                "proposed_time": current_proposal.time
                            }
                        )
                        return
                    else:
                        # 충돌 정보 없는 일반 COUNTER - 기존 로직 유지
                        all_accepted = False
                        counter_proposals.append((participant_id, decision.proposal))
                elif decision.action == MessageType.NEED_HUMAN:
                    self.status = NegotiationStatus.NEED_HUMAN
                    return
            
            # 전원 동의
            if all_accepted:
                self.status = NegotiationStatus.AGREED
                
                # 합의 완료 메시지
                agreed_msg = self._create_message(
                    msg_type=MessageType.ACCEPT,
                    sender_id="system",
                    proposal=current_proposal,
                    message="전원 동의! 일정이 확정되었습니다!"
                )
                yield agreed_msg
                
                # 세션 업데이트
                await self._finalize_agreement(current_proposal)
                return
            
            # 역제안 처리
            if counter_proposals:
                # 교착 상태 체크
                if self._check_deadlock(counter_proposals):
                    self.deadlock_counter += 1
                    if self.deadlock_counter >= 2:
                        self.status = NegotiationStatus.NEED_HUMAN
                        deadlock_msg = self._create_message(
                            msg_type=MessageType.NEED_HUMAN,
                            sender_id="system",
                            message="🔄 같은 제안이 반복되고 있어요. 직접 결정해주세요!"
                        )
                        yield deadlock_msg
                        return
                
                # 가장 최근 역제안을 다음 라운드 제안으로
                _, new_proposal = counter_proposals[-1]
                current_proposal = new_proposal
                
                # 역제안을 이니시에이터 에이전트가 평가
                initiator_decision = await initiator_agent.evaluate_proposal(
                    proposal=current_proposal,
                    context={
                        "round": self.current_round,
                        "participant_count": len(self.participant_user_ids) + 1
                    }
                )
                
                if initiator_decision.action == MessageType.COUNTER:
                    current_proposal = initiator_decision.proposal
                
                response_msg = self._create_message(
                    msg_type=initiator_decision.action,
                    sender_id=self.initiator_user_id,
                    proposal=initiator_decision.proposal,
                    message=initiator_decision.message
                )
                yield response_msg
                await self._save_message(response_msg)
            
            self.current_round += 1
            await asyncio.sleep(0.3)
        
        # 5라운드 초과 → 사용자 개입
        self.status = NegotiationStatus.NEED_HUMAN
        timeout_msg = self._create_message(
            msg_type=MessageType.NEED_HUMAN,
            sender_id="system",
            proposal=current_proposal,
            message="5라운드 협상이 끝났어요. 최종 결정을 내려주세요!"
        )
        yield timeout_msg
    
    def _create_message(
        self,
        msg_type: MessageType,
        sender_id: str,
        proposal: Optional[Proposal] = None,
        message: str = ""
    ) -> A2AMessage:
        """메시지 생성"""
        sender_name = "시스템"
        if sender_id != "system" and sender_id in self.agents:
            sender_name = f"{self.agents[sender_id].user_name}의 AI"
        
        # LLM 응답에서 JSON이 섞여있으면 정리
        cleaned_message = _clean_llm_message(message)
        
        msg = A2AMessage(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            type=msg_type,
            sender_agent_id=sender_id,
            sender_name=sender_name,
            round_number=self.current_round,
            proposal=proposal,
            message=cleaned_message,
            timestamp=datetime.now(KST)
        )
        self.messages.append(msg)
        return msg
    
    async def _save_message(self, msg: A2AMessage):
        """메시지를 주 세션 DB에 저장 (중복 방지 - thread 조회로 모든 참여자가 볼 수 있음)"""
        try:
            receiver_id = None
            if msg.sender_agent_id == self.initiator_user_id:
                receiver_id = self.participant_user_ids[0] if self.participant_user_ids else None
            else:
                receiver_id = self.initiator_user_id
            
            # 주 세션에만 메시지 저장 (중복 방지)
            # thread_id가 설정되어 있으면 모든 참여자가 get_thread_messages로 조회 가능
            await A2ARepository.add_message(
                session_id=self.session_id,  # 주 세션에만 저장
                sender_user_id=msg.sender_agent_id if msg.sender_agent_id != "system" else self.initiator_user_id,
                receiver_user_id=receiver_id,
                message_type=msg.type.value.lower(),
                message={
                    "text": msg.message,
                    "round": msg.round_number,
                    "proposal": msg.proposal.to_dict() if msg.proposal else None
                }
            )
            
            # [NEW] WebSocket 알림: 모든 참여자에게 새 협상 메시지 알림 (실시간 로그 업데이트)
            from src.websocket.websocket_manager import manager as ws_manager
            all_participants = [self.initiator_user_id] + self.participant_user_ids
            logger.info(f"[WS DEBUG] 협상 메시지 알림 전송 시작: 참여자={all_participants}, session_id={self.session_id}")
            for pid in all_participants:
                try:
                    ws_payload = {
                        "type": "a2a_message",
                        "session_id": self.session_id,
                        "message_type": msg.type.value.lower(),
                        "sender_name": msg.sender_name,
                        "message": msg.message[:100] if msg.message else "",
                        "round": msg.round_number
                    }
                    logger.info(f"[WS DEBUG] 전송 시도: {pid} -> {ws_payload}")
                    await ws_manager.send_personal_message(ws_payload, str(pid))
                    logger.info(f"[WS DEBUG] 전송 성공: {pid}")
                except Exception as ws_err:
                    logger.warning(f"[WS] 협상 메시지 알림 전송 실패 ({pid}): {ws_err}")
        except Exception as e:
            logger.error(f"메시지 저장 실패: {e}")
    
    def _check_deadlock(self, counter_proposals: List[tuple]) -> bool:
        """교착 상태 체크 (같은 제안 반복)"""
        for participant_id, proposal in counter_proposals:
            last = self.last_proposals.get(participant_id)
            if last and last.date == proposal.date and last.time == proposal.time:
                return True
            self.last_proposals[participant_id] = proposal
        return False
    
    async def _finalize_agreement(self, proposal: Proposal):
        """합의 확정 - 모든 세션을 사용자 승인 대기 상태로 변경"""
        try:
            logger.info(f"🎉 합의 확정 - 최종 제안: date={proposal.date}, time={proposal.time}, location={proposal.location}")
            
            # 세션 상태를 pending_approval로 업데이트 (사용자가 최종 승인해야 캘린더 등록)
            details = {
                # 원래 요청 시간 (协商 전 사용자가 처음 요청한 시간)
                "requestedDate": self.target_date,
                "requestedTime": self.target_time,
                # 확정 시간 (에이전트 협상 후 최종 합의된 시간)
                "agreedDate": proposal.date,
                "agreedTime": proposal.time,
                # 기존 호환성 유지
                "proposedDate": proposal.date,
                "proposedTime": proposal.time,
                "location": proposal.location,
                "purpose": proposal.activity,
                "agreed_at": datetime.now(KST).isoformat()
            }
            
            # 모든 세션 상태 업데이트 (다중 세션 지원)
            session_ids_to_update = getattr(self, 'all_session_ids', [self.session_id])
            
            for session_id in session_ids_to_update:
                await A2ARepository.update_session_status(
                    session_id, "pending_approval", details
                )
                logger.info(f"세션 {session_id} 협상 완료 - 저장된 details: {details}")
        except Exception as e:
            logger.error(f"합의 확정 실패: {e}")
    
    def get_result(self) -> NegotiationResult:
        """현재 협상 결과 반환"""
        intervention_reason = None
        if self.status == NegotiationStatus.NEED_HUMAN:
            if self.current_round > self.MAX_ROUNDS:
                intervention_reason = HumanInterventionReason.MAX_ROUNDS_EXCEEDED
            elif self.deadlock_counter >= 2:
                intervention_reason = HumanInterventionReason.DEADLOCK
        elif self.status == NegotiationStatus.AWAITING_USER_CHOICE:
            intervention_reason = HumanInterventionReason.CONFLICT_CHOICE_NEEDED
        
        return NegotiationResult(
            status=self.status,
            intervention_reason=intervention_reason,
            total_rounds=self.current_round,
            messages=self.messages,
            awaiting_choice_from=self.awaiting_choice_from if self.awaiting_choice_from else None
        )
