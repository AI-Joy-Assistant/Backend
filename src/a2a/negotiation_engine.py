"""
NegotiationEngine - 다중 참여자 협상 엔진
"""
import logging
import uuid
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .a2a_protocol import (
    MessageType, Proposal, A2AMessage, AgentDecision,
    NegotiationStatus, NegotiationResult, HumanInterventionReason
)
from .personal_agent import PersonalAgent
from .a2a_repository import A2ARepository
from src.auth.auth_repository import AuthRepository

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


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
    
    async def initialize_agents(self):
        """모든 참여자의 에이전트 초기화"""
        all_user_ids = [self.initiator_user_id] + self.participant_user_ids
        
        for user_id in all_user_ids:
            user = await AuthRepository.find_user_by_id(user_id)
            user_name = user.get("name", "사용자") if user else "사용자"
            self.agents[user_id] = PersonalAgent(user_id, user_name)
            logger.info(f"에이전트 초기화: {user_name}")
    
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
                    message="🎉 전원 동의! 일정이 확정되었습니다!"
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
            message="⏰ 5라운드 협상이 끝났어요. 최종 결정을 내려주세요!"
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
        
        msg = A2AMessage(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            type=msg_type,
            sender_agent_id=sender_id,
            sender_name=sender_name,
            round_number=self.current_round,
            proposal=proposal,
            message=message,
            timestamp=datetime.now(KST)
        )
        self.messages.append(msg)
        return msg
    
    async def _save_message(self, msg: A2AMessage):
        """메시지를 DB에 저장"""
        try:
            receiver_id = None
            if msg.sender_agent_id == self.initiator_user_id:
                receiver_id = self.participant_user_ids[0] if self.participant_user_ids else None
            else:
                receiver_id = self.initiator_user_id
            
            await A2ARepository.add_message(
                session_id=self.session_id,
                sender_user_id=msg.sender_agent_id if msg.sender_agent_id != "system" else self.initiator_user_id,
                receiver_user_id=receiver_id,
                message_type=msg.type.value.lower(),
                message={
                    "text": msg.message,
                    "round": msg.round_number,
                    "proposal": msg.proposal.to_dict() if msg.proposal else None
                }
            )
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
        """합의 확정 - 사용자 승인 대기 상태로 변경"""
        try:
            logger.info(f"🎉 합의 확정 - 최종 제안: date={proposal.date}, time={proposal.time}, location={proposal.location}")
            
            # 세션 상태를 pending_approval로 업데이트 (사용자가 최종 승인해야 캘린더 등록)
            details = {
                "proposedDate": proposal.date,
                "proposedTime": proposal.time,
                "location": proposal.location,
                "purpose": proposal.activity,
                "agreed_at": datetime.now(KST).isoformat()
            }
            await A2ARepository.update_session_status(
                self.session_id, "pending_approval", details  # completed → pending_approval
            )
            logger.info(f"세션 {self.session_id} 협상 완료 - 저장된 details: {details}")
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
        
        return NegotiationResult(
            status=self.status,
            intervention_reason=intervention_reason,
            total_rounds=self.current_round,
            messages=self.messages
        )
