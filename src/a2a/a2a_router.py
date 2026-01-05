from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from typing import Optional
import jwt
import json
import asyncio
from config.settings import settings
from config.settings import settings
from .a2a_service import A2AService, convert_relative_date, convert_relative_time
from .a2a_repository import A2ARepository
from .a2a_models import A2ASessionCreate, A2ASessionResponse, A2AMessageResponse
from .negotiation_engine import NegotiationEngine
from .a2a_protocol import NegotiationStatus
from src.auth.auth_service import AuthService
from src.auth.auth_repository import AuthRepository
from src.chat.chat_repository import ChatRepository

router = APIRouter(prefix="/a2a", tags=["A2A"])

def get_current_user_id(request: Request) -> str:
    """JWT 토큰에서 사용자 ID 추출"""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증 토큰이 필요합니다.")
    
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
        return str(user_id)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

@router.post("/session/start", summary="A2A 세션 시작 및 전체 시뮬레이션 실행")
async def start_a2a_session(
    request: A2ASessionCreate,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    A2A 세션을 생성하고 전체 에이전트 간 대화 시뮬레이션을 자동으로 실행합니다.
    백엔드에서 모든 단계를 처리하므로 프론트는 이 API 한 번만 호출하면 됩니다.
    """
    try:
        result = await A2AService.start_a2a_session(
            initiator_user_id=current_user_id,
            target_user_id=request.target_user_id,
            summary=request.summary,
            duration_minutes=request.time_window.get("duration_minutes", 60) if request.time_window else 60,
            origin_chat_session_id=request.origin_chat_session_id
        )
        
        if result["status"] == 200:
            return {
                "session_id": result["session_id"],
                "event": result.get("event"),
                "messages": result.get("messages", [])
            }
        else:
            raise HTTPException(status_code=result["status"], detail=result.get("error", "A2A 세션 시작 실패"))
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"A2A 세션 시작 실패: {str(e)}")

@router.get("/session/{session_id}", summary="A2A 세션 조회")
async def get_a2a_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """A2A 세션 정보 조회"""
    try:
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 권한 확인 (세션 참여자만 조회 가능)
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="세션 접근 권한이 없습니다.")
        
        # Details 구성
        # place_pref에서 thread_id 확인
        place_pref = session.get("place_pref", {}) or {}
        import json
        if isinstance(place_pref, str):
            try:
                place_pref = json.loads(place_pref)
            except:
                place_pref = {}
        
        thread_id = place_pref.get("thread_id")
        
        # 1. 메시지 조회하여 Process 구성
        # thread_id가 있으면 thread의 모든 메시지 조회 (모든 참여자에게 동일한 로그 표시)
        if thread_id:
            messages = await A2ARepository.get_thread_messages(thread_id)
        else:
            messages = await A2ARepository.get_session_messages(session_id)
        
        # 발신자 이름 조회를 위한 사용자 정보 캐시
        user_names_cache = {}
        all_user_ids = set()
        for msg in messages:
            sender_id = msg.get("sender_user_id")
            if sender_id:
                all_user_ids.add(sender_id)
        
        if all_user_ids:
            from src.chat.chat_repository import ChatRepository
            user_names_cache = await ChatRepository.get_user_names_by_ids(list(all_user_ids))
        
        process = []
        for msg in messages:
            msg_data = msg.get("message", {}) or {}
            created_at = msg.get("created_at")  # 메시지 생성 시간
            
            # 발신자 정보
            sender_id = msg.get("sender_user_id")
            sender_name = user_names_cache.get(sender_id, "AI") if sender_id else "시스템"
            
            # 메시지 타입 확인
            msg_type = msg_data.get("type") or msg.get("type")
            
            # 재조율 요청 메시지 처리
            if msg_type == "reschedule_request":
                process.append({
                    "step": "🔄 재조율 요청",
                    "description": f"{sender_name}님이 재조율을 요청했습니다. ({msg_data.get('reason', '')})",
                    "created_at": created_at
                })
                continue
            
            # [DISABLED] 충돌 경고 메시지 처리 - 협상 로그에 표시하지 않음
            if msg_type == "conflict_warning":
                continue  # 충돌 알림은 로그에서 제외
                # process.append({
                #     "step": "⚠️ 시간 충돌 알림",
                #     "description": msg_data.get("description", "같은 시간대에 다른 일정이 확정되었습니다. 재조율이 필요합니다."),
                #     "created_at": created_at,
                #     "type": "conflict_warning"
                # })
                # continue
            
            # 기존 형식: step + text
            step = msg_data.get("step")
            text = msg_data.get("text")
            
            # True A2A 형식: round + text + proposal
            round_num = msg_data.get("round")
            proposal = msg_data.get("proposal")
            
            if step and text:
                # 기존 형식
                process.append({"step": str(step), "description": text, "created_at": created_at})
            elif text:
                # True A2A 형식 - 발신자 표시 추가
                step_label = f"[{sender_name}의 AI] Round {round_num}" if round_num else f"[{sender_name}의 AI]"
                description = text
                # proposal이 있을 때만 날짜/시간 표시
                if proposal and (proposal.get('date') or proposal.get('time')):
                    proposal_info = f" ({proposal.get('date', '')} {proposal.get('time', '')})"
                    description += proposal_info
                process.append({"step": step_label, "description": description, "created_at": created_at})
        
        # 2. 기본 정보
        place_pref = session.get("place_pref", {}) or {}
        time_window = session.get("time_window", {}) or {}

        # JSON 파싱 (문자열로 저장된 경우)
        import json
        if isinstance(place_pref, str):
            try: place_pref = json.loads(place_pref)
            except: place_pref = {}
        if isinstance(time_window, str):
            try: time_window = json.loads(time_window)
            except: time_window = {}
            
        summary = place_pref.get("summary") or session.get("summary")
        
        # Initiator 정보 조회
        initiator_id = session.get("initiator_user_id")
        initiator_name = "알 수 없음"
        initiator_avatar = "https://picsum.photos/150"
        
        if initiator_id == current_user_id:
            initiator_name = "나"
            # 내 정보 조회 (프로필 이미지를 위해)
            try:
                initiator_user = await AuthRepository.find_user_by_id(initiator_id)
                if initiator_user:
                    initiator_avatar = initiator_user.get("profile_image") or initiator_avatar
            except:
                pass
        elif initiator_id:
            try:
                # AuthRepository가 상단에 import 되어 있다고 가정 (line 6)
                initiator_user = await AuthRepository.find_user_by_id(initiator_id)
                if initiator_user:
                    initiator_name = initiator_user.get("name") or initiator_user.get("email") or "알 수 없음"
                    initiator_avatar = initiator_user.get("profile_image") or initiator_avatar
            except Exception as e:
                print(f"Initiator 조회 실패: {e}")
        
        details = {
            "proposer": initiator_name,
            "proposerAvatar": initiator_avatar,
            "purpose": place_pref.get("purpose") or summary or "일정 조율",
            # 원래 요청 시간 (변경되지 않음)
            "requestedDate": place_pref.get("requestedDate") or place_pref.get("date") or time_window.get("date") or "",
            "requestedTime": place_pref.get("requestedTime") or place_pref.get("time") or time_window.get("time") or "미정",
            # 제안/확정 시간 (협상 결과)
            "proposedDate": place_pref.get("proposedDate") or place_pref.get("date") or time_window.get("date") or "",
            "proposedTime": place_pref.get("proposedTime") or place_pref.get("time") or time_window.get("time") or "미정",
            # 확정 시간 (에이전트 협상 후)
            "agreedDate": place_pref.get("agreedDate") or "",
            "agreedTime": place_pref.get("agreedTime") or "",
            "location": place_pref.get("location") or "미정",
            "process": process,
            # [FIX] place_pref에서 충돌 정보 읽어오기
            "has_conflict": place_pref.get("has_conflict", False),
            "conflicting_sessions": place_pref.get("conflicting_sessions", []),
            "conflicting_event": place_pref.get("conflicting_event"), # 레거시 호환
            # 종료 시간 (시간 범위 지원)
            "proposedEndDate": place_pref.get("proposedEndDate") or "",
            "proposedEndTime": place_pref.get("proposedEndTime") or "",
            "agreedEndDate": place_pref.get("agreedEndDate") or "",
            "agreedEndTime": place_pref.get("agreedEndTime") or "",
            # 재조율 요청 정보
            "rescheduleRequestedBy": place_pref.get("rescheduleRequestedBy"),
            "rescheduleRequestedAt": place_pref.get("rescheduleRequestedAt"),  # [NEW] 재조율 요청 시간
            "rescheduleReason": place_pref.get("rescheduleReason"),
            # 나간 참여자 정보 (거절한 사람들)
            "left_participants": place_pref.get("left_participants", []),
            # [NEW] 다박 일정 정보 - 1박 이상이면 시간 대신 날짜 범위 표시
            "duration_nights": place_pref.get("duration_nights", 0)
        }
        
        # [PERFORMANCE] 캘린더 충돌 확인 비활성화 - Google Calendar API 호출이 ~1초 소요됨
        # 필요시 별도 API(/a2a/session/{id}/conflicts)로 분리하여 비동기 로드 권장
        # try:
        #     proposed_date = details.get("proposedDate")
        #     proposed_time = details.get("proposedTime")
        #     
        #     if proposed_date and proposed_time and proposed_time != "미정":
        #         ... (캘린더 충돌 확인 로직)
        # except Exception as conflict_error:
        #     print(f"충돌 확인 오류: {conflict_error}")

        
        # 디버깅: 추출된 날짜 확인
        session_status = session.get("status", "unknown")
        print(f"Session {session_id} - status: {session_status}, date: {details['proposedDate']}, time: {details['proposedTime']}, conflict: {details['has_conflict']}")
        
        # 참여자 정보 추가 (Attendees) - 다중 참여자 지원
        attendees = []
        added_ids = set()  # 중복 방지
        approved_user_ids = set()  # 승인한 사용자 ID 목록
        
        try:
            # 1. participant_user_ids 컬럼 우선 사용 (새 방식)
            participant_ids = session.get("participant_user_ids") or []
            
            # 2. 없으면 initiator + target fallback (기존 세션 호환)
            if not participant_ids:
                if initiator_id:
                    participant_ids.append(initiator_id)
                target_id = session.get("target_user_id")
                if target_id and target_id != initiator_id:
                    participant_ids.append(target_id)
            
            print(f"🔍 [Attendees] participant_user_ids: {participant_ids}")
            
            # place_pref에서 left_participants 가져오기
            left_participants = place_pref.get("left_participants", [])
            print(f"🔍 [Attendees] left_participants: {left_participants}")
            
            # [NEW] 승인된 사용자 목록 조회 (place_pref에서 approved_by_list 확인)
            if session_status in ["pending_approval", "in_progress", "pending", "needs_reschedule", "awaiting_user_choice"]:
                # 1. 명시적 승인 목록 추가
                approved_list = place_pref.get("approved_by_list", [])
                if approved_list:
                    for uid in approved_list:
                        approved_user_ids.add(str(uid))
                    print(f"🔍 [Attendees] place_pref approved_by_list: {approved_list}")
                
                # 2. 요청자(Initiator 또는 Rescheduler) 자동 추가 (항상 승인 상태)
                # approved_by_list의 유무와 관계없이, 제안자는 항상 승인자로 포함해야 함
                reschedule_requested_by = place_pref.get("rescheduleRequestedBy")
                if reschedule_requested_by:
                    approved_user_ids.add(str(reschedule_requested_by))
                elif initiator_id:
                    # 원래 요청자(initiator)는 자동 승인
                    approved_user_ids.add(str(initiator_id))
            elif session_status == "completed":
                # 완료된 세션은 모든 참여자가 승인됨
                for pid in participant_ids:
                    if pid not in left_participants:
                        approved_user_ids.add(str(pid))
            
            print(f"🔍 [Attendees] approved_user_ids: {approved_user_ids}")
            
            # 3. 모든 참여자 정보 조회 (나간 사람 제외)
            for participant_id in participant_ids:
                # 나간 참여자는 제외
                if participant_id in left_participants:
                    print(f"🔍 [Attendees] Skipping left participant: {participant_id}")
                    continue
                    
                if participant_id and participant_id not in added_ids:
                    try:
                        participant_info = await AuthRepository.find_user_by_id(participant_id)
                        if participant_info:
                            attendees.append({
                                "id": participant_id,
                                "name": participant_info.get("name") or "알 수 없음",
                                "avatar": participant_info.get("profile_image") or "https://picsum.photos/150",
                                "isCurrentUser": participant_id == current_user_id,
                                "is_approved": str(participant_id) in approved_user_ids  # NEW
                            })
                            added_ids.add(participant_id)
                    except Exception as e:
                        print(f"참여자 조회 실패 ({participant_id}): {e}")
        except Exception as e:
            print(f"참여자 정보 조회 오류: {e}")
        
        print(f"📋 [Attendees Final] Total: {len(attendees)}, IDs: {added_ids}")
        details["attendees"] = attendees
        details["approved_user_ids"] = list(approved_user_ids)  # NEW


        session["details"] = details
        session["title"] = summary if summary else "일정 조율"
        session["summary"] = summary

        return A2ASessionResponse(**session)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세션 조회 실패: {str(e)}")

@router.get("/session/{session_id}/messages", summary="A2A 세션의 에이전트 간 대화 메시지 조회")
async def get_a2a_messages(
    session_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """A2A 세션의 모든 에이전트 간 대화 메시지 조회"""
    try:
        # 세션 존재 및 권한 확인
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="세션 접근 권한이 없습니다.")
        
        # thread_id 확인
        place_pref = session.get("place_pref", {})
        thread_id = None
        if isinstance(place_pref, dict):
            thread_id = place_pref.get("thread_id")
        
        # thread_id가 있으면 thread의 모든 메시지 조회 (단체 채팅방)
        if thread_id:
            messages = await A2ARepository.get_thread_messages(thread_id)
        else:
            # thread_id가 없으면 해당 세션의 메시지만 조회 (1:1 채팅방)
            messages = await A2ARepository.get_session_messages(session_id)
        
        # Supabase에서 가져온 데이터를 A2AMessageResponse 형식으로 변환
        formatted_messages = []
        for msg in messages:
            # Supabase의 필드명을 모델 필드명에 맞게 변환
            formatted_msg = {
                "id": str(msg.get("id", "")),
                "session_id": str(msg.get("session_id", "")),
                "sender_user_id": str(msg.get("sender_user_id", "")),
                "receiver_user_id": str(msg.get("receiver_user_id", "")),
                "message_type": str(msg.get("type", msg.get("message_type", ""))),
                "message": msg.get("message", {}),  # JSONB 필드는 그대로 유지
                "created_at": msg.get("created_at", "")
            }
            formatted_messages.append(A2AMessageResponse(**formatted_msg))
        
        return {
            "session_id": session_id,
            "thread_id": thread_id,
            "messages": formatted_messages
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"메시지 조회 실패: {str(e)}")

@router.get("/sessions", summary="사용자의 A2A 세션 목록 조회")
async def get_user_sessions(
    current_user_id: str = Depends(get_current_user_id)
):
    """현재 사용자가 참여한 모든 A2A 세션 목록 조회 (thread_id 기준으로 그룹화)"""
    try:
        sessions = await A2ARepository.get_user_sessions(current_user_id)
        
        # thread_id 기준으로 그룹화
        from collections import defaultdict
        sessions_by_thread = defaultdict(list)
        
        for session in sessions:
            place_pref = session.get("place_pref", {})
            thread_id = None
            if isinstance(place_pref, dict):
                thread_id = place_pref.get("thread_id")
                
                # [NEW] hidden_by 확인 - 현재 사용자가 숨긴 세션이면 건너뛰기
                hidden_by = place_pref.get("hidden_by", [])
                if current_user_id in hidden_by:
                    continue  # 이 세션은 현재 사용자 목록에서 제외
            
            # thread_id가 없으면 세션 ID를 thread_id로 사용 (1:1 세션)
            if not thread_id:
                thread_id = session.get("id")
            
            sessions_by_thread[thread_id].append(session)
        
        # 각 thread 그룹에서 대표 세션 선택 (가장 최근 세션)
        grouped_sessions = []
        all_participant_ids = set()
        for thread_id, thread_sessions in sessions_by_thread.items():
            # 가장 최근 세션을 대표로 사용
            representative = max(thread_sessions, key=lambda x: x.get('created_at', ''))

            # 참여자 ID 수집 (initiator + target + participant_user_ids)
            initiators = {s.get("initiator_user_id") for s in thread_sessions}
            targets = {s.get("target_user_id") for s in thread_sessions}
            
            # session.participant_user_ids에서 참여자 수집 (다중 사용자 세션 지원)
            session_participants = set()
            for s in thread_sessions:
                p_ids = s.get("participant_user_ids") or []
                if isinstance(p_ids, list):
                    session_participants.update(p_ids)

            # place_pref에 명시된 참여자 정보도 확인 (UUID 형식인 것만 필터링)
            place_pref = representative.get("place_pref", {})
            pref_participants = set()
            if isinstance(place_pref, dict) and place_pref.get("participants"):
                import re
                uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
                for p in place_pref.get("participants", []):
                    if isinstance(p, str) and uuid_pattern.match(p):
                        pref_participants.add(p)

            # 전체 참여자 합집합 (나 제외)
            participants_set = (initiators | targets | pref_participants | session_participants) - {current_user_id}

            participant_list = list(participants_set)
            all_participant_ids.update(participants_set) # 전체 ID 수집

            # 대표 세션 객체에 정보 주입
            representative["thread_id"] = thread_id
            representative["participant_ids"] = participant_list
            representative["participant_count"] = len(participant_list)
            
            grouped_sessions.append(representative)
        
        # 최근 순으로 정렬
        grouped_sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # 3. 상세 정보 일괄 조회 (DB 부하 감소)
        user_details_map = {}
        if all_participant_ids:
            user_details_map = await ChatRepository.get_user_details_by_ids(list(all_participant_ids))

        # 4. 이름 매핑 적용 (나간 참여자 제외)
        for session in grouped_sessions:
            p_ids = session.get("participant_ids", [])
            
            # left_participants 추출
            place_pref = session.get("place_pref", {}) or {}
            if isinstance(place_pref, str):
                try:
                    place_pref = json.loads(place_pref)
                except:
                    place_pref = {}
            left_participants = place_pref.get("left_participants", [])
            
            p_names = []
            for pid in p_ids:
                # 나간 참여자는 제외
                if pid in left_participants:
                    continue
                user_info = user_details_map.get(pid, {})
                name = user_info.get("name", "알 수 없음")
                p_names.append(name)

            # 이름이 없으면(탈퇴 등) '상대 없음'로 표시
            if not p_names:
                p_names = ["상대 없음"]

            session["participant_names"] = p_names

        # 5. 최신순 정렬
        grouped_sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # 6. 추가 정보(title, details) 구성
        final_sessions = []
        for session in grouped_sessions:
            # 기본 정보
            place_pref = session.get("place_pref", {}) or {}
            # place_pref가 문자열로 저장된 경우 JSON 파싱
            if isinstance(place_pref, str):
                try:
                    place_pref = json.loads(place_pref)
                except:
                    place_pref = {}
            if not isinstance(place_pref, dict):
                place_pref = {}
                
            # print(f"📌 [get_a2a_sessions] Session {session.get('id')}: place_pref = {place_pref}")
            
            summary = place_pref.get("summary") or session.get("summary")
            
            # Title
            p_names = session.get("participant_names", [])
            title = summary if summary else f"{', '.join(p_names)}와의 약속"
            
            # Details 구성
            # Initiator 이름 및 아바타 찾기
            initiator_id = session.get("initiator_user_id")
            initiator_name = "알 수 없음"
            initiator_avatar = "https://picsum.photos/150"
            
            if initiator_id == current_user_id:
                initiator_name = "나"
                if initiator_id in user_details_map:    
                    user_info = user_details_map[initiator_id]
                    initiator_avatar = user_info.get("profile_image") or initiator_avatar
            elif initiator_id in user_details_map:
                user_info = user_details_map[initiator_id]
                initiator_name = user_info.get("name", "알 수 없음")
                initiator_avatar = user_info.get("profile_image") or initiator_avatar
            
            # [OPTIMIZED] 목록 조회 시에는 메시지(process)를 가져오지 않음 (성능 최적화)
            # 상세 조회(handleLogClick) 시에만 메시지를 가져옴
            process = []  # 빈 배열 반환
            
            # left_participants 정보 추출
            left_participants = place_pref.get("left_participants", [])
            
            # [NEW] conflict_reason이 있으면 프로세스에 경고 추가
            conflict_reason = place_pref.get("conflict_reason")
            if conflict_reason:
                process.append({
                    "step": "⚠️ 충돌 알림",
                    "description": conflict_reason,
                    "type": "conflict_warning"
                })
            
            # place_pref에서 직접 날짜/시간 정보 추출 (details 컬럼은 DB에 없음)
            # 재조율 시 proposedDate/proposedTime 키, 초기 생성 시 date/time 키 사용
            proposed_date = place_pref.get("proposedDate") or place_pref.get("date")
            proposed_time = place_pref.get("proposedTime") or place_pref.get("time") or "미정"
            
            # [OPTIMIZED] 충돌 감지: 메모리 내에서 비교 (N개 DB 쿼리 대신)
            # [FIX] DB에 저장된 충돌 정보 우선 로드
            db_has_conflict = place_pref.get("has_conflict", False)
            db_conflicts = place_pref.get("conflicting_sessions", [])
            if not isinstance(db_conflicts, list): db_conflicts = []
            
            session_status = session.get("status", "").lower()
            session_id = session.get("id")
            
            # [FIX] 현재 세션이 rejected 또는 completed 상태이면 충돌 체크 안 함
            # 거절된 세션은 더 이상 활성 상태가 아니므로 충돌로 표시하면 안 됨
            if session_status in ["rejected", "completed"]:
                has_conflict = False
                conflicting_sessions = []
            else:
                # [FIX] 현재 세션의 thread_id (같은 thread = 같은 일정 요청)
                my_thread_id = place_pref.get("thread_id") or session_id
                
                # [NEW] DB에 저장된 충돌 세션의 제목을 동적으로 보완
                # 기존 데이터에 "일정" 또는 "확정된 일정"만 있는 경우 실제 제목으로 대체
                # [FIX] rejected 상태인 세션 및 같은 thread_id인 세션은 충돌 목록에서 제외
                enriched_conflicts = []
                for conflict in db_conflicts:
                    conflict_id = conflict.get("id") or conflict.get("session_id")
                    conflict_title = conflict.get("title", "")
                    
                    # grouped_sessions에서 해당 세션을 찾아서 상태 확인 및 제목 보완
                    conflict_session = None
                    for gs in grouped_sessions:
                        if gs.get("id") == conflict_id:
                            conflict_session = gs
                            break
                    
                    # [FIX] 충돌 세션의 상태가 rejected면 충돌 목록에서 제외
                    if conflict_session:
                        conflict_status = conflict_session.get("status", "").lower()
                        if conflict_status == "rejected":
                            continue  # rejected 상태인 세션은 충돌로 간주하지 않음
                        
                        # [FIX] 같은 thread_id인 세션은 같은 일정 요청의 일부이므로 충돌 아님
                        conflict_pref = conflict_session.get("place_pref", {})
                        if isinstance(conflict_pref, str):
                            try: conflict_pref = json.loads(conflict_pref)
                            except: conflict_pref = {}
                        conflict_thread_id = conflict_pref.get("thread_id") or conflict_id
                        if my_thread_id == conflict_thread_id:
                            continue  # 같은 일정 그룹은 충돌로 보지 않음
                    
                    # 제목 보완 (제목이 없거나 기본값인 경우)
                    if conflict_session and (not conflict_title or conflict_title in ["일정", "확정된 일정", "새 일정"]):
                        gs_pref = conflict_session.get("place_pref", {})
                        if isinstance(gs_pref, str):
                            try: gs_pref = json.loads(gs_pref)
                            except: gs_pref = {}
                        
                        # 제목 결정 (purpose > summary > 참여자 이름)
                        new_title = gs_pref.get("purpose") or gs_pref.get("summary") or gs_pref.get("activity")
                        if not new_title:
                            p_names = conflict_session.get("participant_names", [])
                            if p_names:
                                new_title = f"{', '.join(p_names)}와 약속"
                        
                        if new_title:
                            conflict_title = new_title
                        
                        # 참여자 이름도 보완
                        if not conflict.get("participant_names"):
                            conflict["participant_names"] = conflict_session.get("participant_names", [])
                    
                    conflict["title"] = conflict_title or "일정"
                    enriched_conflicts.append(conflict)
                
                # [FIX] 필터링된 충돌 목록 기반으로 has_conflict 재계산
                # DB에 저장된 충돌 정보 중 rejected 상태가 된 세션은 제외되었으므로 길이 기반으로 판단
                has_conflict = len(enriched_conflicts) > 0
                conflicting_sessions = enriched_conflicts
            
            if proposed_date and session_status in ["pending", "in_progress", "pending_approval", "needs_reschedule"]:
                import re
                from datetime import datetime as dt
                
                # 날짜/시간 정규화 함수 (인라인)
                def norm_date(d):
                    if not d: return ""
                    m = re.search(r'(\d{1,2})월\s*(\d{1,2})일', d)
                    if m: return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
                    m = re.search(r'\d{4}-(\d{2})-(\d{2})', d)
                    if m: return f"{m.group(1)}-{m.group(2)}"
                    return d
                
                def norm_time(t):
                    if not t: return -1
                    t = t.replace(" ", "")
                    m = re.search(r'(\d{1,2}):\d{2}', t)
                    if m: return int(m.group(1))
                    is_pm = "오후" in t
                    m = re.search(r'(\d{1,2})시', t)
                    if m:
                        h = int(m.group(1))
                        if is_pm and h != 12: h += 12
                        elif not is_pm and h == 12: h = 0
                        return h
                    return -1
                
                my_date = norm_date(proposed_date)
                my_hour = norm_time(proposed_time)
                
                # print(f"🔍 [충돌체크] session={session_id[:8]}, proposed_date={proposed_date}, proposed_time={proposed_time}, my_date={my_date}, my_hour={my_hour}")
                
                # 시간이 유효하면 충돌 비교 실행 (과거 날짜 스킵 제거 - 연도 경계 문제 방지)
                if my_hour >= 0:
                    # 동일 날짜+시간 세션 찾기 (디버그)
                    same_time_sessions = [s for s in grouped_sessions if s.get("id") != session_id]
                    # print(f"🔍 [충돌비교] session={session_id[:8]}, 날짜={my_date}, 시간={my_hour}, 비교대상={len(same_time_sessions)}개")
                    
                    # [FIX] 현재 세션의 thread_id 가져오기 (같은 thread = 같은 일정 요청)
                    my_thread_id = place_pref.get("thread_id") or session_id
                    
                    for other in grouped_sessions:
                        if other.get("id") == session_id:
                            continue
                        
                        # [FIX] 같은 thread_id인 세션은 같은 일정 요청의 일부이므로 충돌 아님
                        other_pref = other.get("place_pref", {})
                        if isinstance(other_pref, str):
                            try: other_pref = json.loads(other_pref)
                            except: continue
                        
                        other_thread_id = other_pref.get("thread_id") or other.get("id")
                        if my_thread_id == other_thread_id:
                            continue  # 같은 일정 그룹은 충돌로 보지 않음
                        
                        other_status = other.get("status", "").lower()
                        if other_status not in ["pending", "in_progress", "pending_approval", "needs_reschedule", "completed"]:
                            continue
                        
                        other_date = other_pref.get("proposedDate") or other_pref.get("date") or ""
                        other_time = other_pref.get("proposedTime") or other_pref.get("time") or ""
                        other_date_norm = norm_date(other_date)
                        other_hour = norm_time(other_time)
                        
                        if other_date_norm == my_date and other_hour >= 0 and other_hour == my_hour:
                            # print(f"✅ [충돌발견] {session_id[:8]} <-> {other.get('id')[:8]}, 날짜={my_date}, 시간={my_hour}")
                            # [FIX] 중복 추가 방지
                            # stored items might use 'session_id', dynamic uses 'id'
                            is_dup = False
                            other_id = other.get("id")
                            for c in conflicting_sessions:
                                if c.get("id") == other_id or c.get("session_id") == other_id:
                                    is_dup = True
                                    break
                            
                            if not is_dup:
                                # 충돌 세션의 제목 결정 (purpose > summary > 참여자 이름 기반)
                                conflict_title = (
                                    other_pref.get("purpose") or 
                                    other_pref.get("summary") or 
                                    other_pref.get("activity")
                                )
                                # 제목이 없으면 참여자 이름으로 생성
                                if not conflict_title:
                                    participant_names = other.get("participant_names", [])
                                    if participant_names:
                                        conflict_title = f"{', '.join(participant_names)}와 약속"
                                    else:
                                        conflict_title = "일정"
                                
                                conflicting_sessions.append({
                                    "id": other_id,
                                    "title": conflict_title,
                                    "date": other_date,
                                    "time": other_time,
                                    "participant_names": other.get("participant_names", [])
                                })
                    
                    has_conflict = len(conflicting_sessions) > 0
            
            details = {
                "proposer": initiator_name,
                "proposerAvatar": initiator_avatar,
                "purpose": place_pref.get("purpose") or summary or "일정 조율",
                "proposedTime": proposed_time,
                "proposedDate": proposed_date,
                "location": place_pref.get("location") or "미정",
                "process": process,
                "has_conflict": has_conflict,
                "conflicting_sessions": conflicting_sessions,
                "left_participants": left_participants,  # 프론트엔드 필터링용
                # [NEW] 다박 일정 정보 - 1박 이상이면 시간 대신 날짜 범위 표시
                "duration_nights": place_pref.get("duration_nights", 0)
            }

            session["title"] = title
            session["summary"] = summary
            session["details"] = details
            
            final_sessions.append(A2ASessionResponse(**session))


        # 7. 지난 일정 필터링 (자동 삭제)
        active_sessions = []
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import re
        
        KST = ZoneInfo("Asia/Seoul")
        now = datetime.now(KST)
        
        for session in final_sessions:
            details = session.details
            if not details:
                active_sessions.append(session)
                continue
                
            p_date = details.get("proposedDate")
            p_time = details.get("proposedTime")
            
            # 날짜와 시간이 모두 있는 경우에만 필터링 체크
            if p_date and p_time and p_time != "미정":
                try:
                    target_date_str = None
                    
                    # 1. 날짜 파싱
                    # "12월 13일" 같은 한글 형식 처리
                    korean_date_match = re.match(r'(\d+)월\s*(\d+)일', p_date)
                    if korean_date_match:
                        month = int(korean_date_match.group(1))
                        day = int(korean_date_match.group(2))
                        year = now.year
                        
                        # 연도 보정 로직:
                        # 1. 과거 날짜 처리: 현재 월보다 이전이거나 같은 달의 지난 날 → 내년
                        # 2. 너무 먼 미래 처리: 현재 월보다 3개월 이상 큰 월 → 작년
                        current_month = now.month
                        if month < current_month or (month == current_month and day < now.day):
                            year = now.year + 1
                        elif month > current_month + 3:
                            year = now.year - 1
                        target_date_str = f"{year}-{month:02d}-{day:02d}"
                    elif re.match(r'^\d{4}-\d{2}-\d{2}$', p_date):
                        target_date_str = p_date
                    else:
                        # 변환 불가능하면 유지
                        active_sessions.append(session)
                        continue

                    # 2. 시간 파싱 (헬퍼 함수 사용 - 시간은 안전함)
                    normalized_time = convert_relative_time(p_time) or p_time
                    
                    if target_date_str and normalized_time and ':' in normalized_time:
                         # datetime 객체 생성
                        hour, minute = map(int, normalized_time.split(':'))
                        dt_str = f"{target_date_str}T{hour:02d}:{minute:02d}:00"
                        event_dt = datetime.fromisoformat(dt_str).replace(tzinfo=KST)
                        
                        # 현재 시간보다 미래인 경우만 추가
                        if event_dt > now:
                            active_sessions.append(session)
                        else:
                            pass  # 과거 이벤트 필터링됨
                    else:
                        active_sessions.append(session)
                        
                except Exception as e:
                    print(f"⚠️ [Auto-Delete] Date parse error for session {session.id}: {e}")
                    active_sessions.append(session)
            else:
                # 날짜/시간이 미정인 경우 (조율 중) 표시
                active_sessions.append(session)

        return {
            "sessions": active_sessions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세션 목록 조회 실패: {str(e)}")

@router.get("/pending-requests", summary="사용자에게 온 일정 요청 조회")
async def get_pending_requests(
    current_user_id: str = Depends(get_current_user_id)
):
    """
    현재 사용자에게 온 pending 상태의 일정 요청 목록 조회
    - 내가 target_user_id인 세션만 조회
    - status가 'pending' 또는 'pending_approval'인 세션만 반환
    """
    try:
        print(f"🔍 [Pending Requests] Fetching for user: {current_user_id}")
        sessions = await A2ARepository.get_pending_requests_for_user(current_user_id)
        raw_count = len(sessions) if sessions else 0
        
        # [OPTIMIZED] 날짜 기반 사전 필터링: 오늘 이전 날짜의 세션 제외
        if sessions:
            import json
            import re
            from datetime import datetime as dt
            today = dt.now().date()
            
            filtered_sessions = []
            for session in sessions:
                place_pref = session.get("place_pref", {}) or {}
                if isinstance(place_pref, str):
                    try:
                        place_pref = json.loads(place_pref)
                    except:
                        place_pref = {}
                
                # 날짜 추출 (우선순위: proposedDate > date)
                proposed_date = None
                if isinstance(place_pref, dict):
                    proposed_date = place_pref.get("proposedDate") or place_pref.get("date")
                
                # 날짜가 없으면 조율 중이므로 포함
                if not proposed_date:
                    filtered_sessions.append(session)
                    continue
                
                # 날짜 파싱 및 필터링
                try:
                    is_future_or_today = True  # 기본값: 표시
                    
                    # 1. YYYY-MM-DD 형식
                    if "-" in proposed_date and len(proposed_date.split("-")) == 3:
                        p_date_obj = dt.strptime(proposed_date, "%Y-%m-%d").date()
                        is_future_or_today = p_date_obj >= today
                    
                    # 2. 한글 날짜 형식 (예: "1월 1일")
                    elif "월" in proposed_date and "일" in proposed_date:
                        match = re.search(r'(\d+)월\s*(\d+)일', proposed_date)
                        if match:
                            month, day = map(int, match.groups())
                            try:
                                p_date_obj = dt(today.year, month, day).date()
                                is_future_or_today = p_date_obj >= today
                            except:
                                pass
                    
                    if is_future_or_today:
                        filtered_sessions.append(session)
                except:
                    # 파싱 실패 시 안전하게 포함
                    filtered_sessions.append(session)
            
            sessions = filtered_sessions
        
        print(f"🔍 [Pending Requests] DB: {raw_count}개 → 필터링 후: {len(sessions)}개 (오늘 이전 제외)")
        
        if not sessions:
            return {"requests": []}
        
        # 1. 모든 관련 사용자 ID 수집 (참여자 정보 일괄 조회를 위해)
        all_user_ids = set()
        for session in sessions:
            p_ids = session.get("participant_user_ids")
            if not p_ids:
                p_ids = [session.get("initiator_user_id"), session.get("target_user_id")]
            for uid in p_ids:
                if uid: all_user_ids.add(str(uid))
        
        user_details_map = {}
        if all_user_ids:
            user_details_map = await ChatRepository.get_user_details_by_ids(list(all_user_ids))
        
        # 응답 데이터 구성
        requests = []
        for session in sessions:
            # [FILTER] 완료되었거나 진행 중(시스템 처리 중)인 세션 숨기기
            status = session.get("status")
            if status in ['completed', 'rejected', 'in_progress', 'failed']:
                continue

            # place_pref 파싱
            place_pref = session.get("place_pref", {}) or {}
            if isinstance(place_pref, str):
                try:
                    import json
                    place_pref = json.loads(place_pref)
                except:
                    place_pref = {}
            
            # [FILTER] 내 행동이 필요한지 확인 (My Turn)
            # 내가 이미 승인했거나(보낸 사람), 내가 처리할 차례가 아니면 숨김
            is_action_required = False
            approved_list = place_pref.get("approved_by_list", [])
            # ID 문자열 변환 보장
            approved_ids = set(str(uid) for uid in approved_list)
            
            if status == 'pending':
                # 단순 요청: 대상자만 볼 수 있음
                if str(session.get("target_user_id")) == str(current_user_id):
                    is_action_required = True
            elif status in ['pending_approval', 'needs_reschedule', 'awaiting_user_choice']:
                # 승인 대기 / 재조율: 아직 승인하지 않은 참여자만 볼 수 있음
                # (재조율 신청자는 reschedule_session에서 approved_by_list에 자동 추가됨)
                if str(current_user_id) not in approved_ids:
                    is_action_required = True
            
            if not is_action_required:
                continue

            thread_id = place_pref.get("thread_id") if isinstance(place_pref, dict) else None
            summary = place_pref.get("summary") if isinstance(place_pref, dict) else None
            
            # 요청자 정보
            initiator_id = session.get("initiator_user_id")
            initiator_info = user_details_map.get(initiator_id, {})
            initiator_name = initiator_info.get("name", "알 수 없음")
            initiator_avatar = initiator_info.get("profile_image", "https://picsum.photos/150")
            
            # 참여자 목록 구성 및 승인 여부 계산
            participant_ids = session.get("participant_user_ids") or [initiator_id, session.get("target_user_id")]
            participant_count = len(participant_ids)
            
            # [Copied Logic] 승인된 사용자 목록 계산
            approved_user_ids = set()
            session_status = session.get("status")
            if session_status in ["pending_approval", "in_progress", "pending", "needs_reschedule", "awaiting_user_choice"]:
                # 1. 명시적 승인 목록
                approved_list = place_pref.get("approved_by_list", [])
                if approved_list:
                    for uid in approved_list:
                        approved_user_ids.add(str(uid))
                
                # 2. 요청자/제안자 자동 승인
                reschedule_requested_by = place_pref.get("rescheduleRequestedBy")
                if reschedule_requested_by:
                    approved_user_ids.add(str(reschedule_requested_by))
                elif initiator_id:
                    approved_user_ids.add(str(initiator_id))
            elif session_status == "completed":
                 for pid in participant_ids:
                     approved_user_ids.add(str(pid))

            # Attendees 리스트 생성
            attendees = []
            for pid in participant_ids:
                p_info = user_details_map.get(pid, {})
                attendees.append({
                    "id": pid,
                    "name": p_info.get("name", "알 수 없음"),
                    "avatar": p_info.get("profile_image", "https://picsum.photos/150"),
                    "isCurrentUser": str(pid) == str(current_user_id),
                    "is_approved": str(pid) in approved_user_ids
                })

            # 날짜/시간 정보 (우선순위: details > place_pref)
            proposed_date, proposed_time = None, None
            details = session.get("details", {}) or {}
            if isinstance(details, str):
                try: import json; details = json.loads(details)
                except: details = {}
            
            if isinstance(details, dict):
                proposed_date = details.get("proposedDate")
                proposed_time = details.get("proposedTime")
            
            if not proposed_date or not proposed_time:
                if isinstance(place_pref, dict):
                    proposed_date = proposed_date or place_pref.get("proposedDate") or place_pref.get("date")
                    proposed_time = proposed_time or place_pref.get("proposedTime") or place_pref.get("time")
            
            # NOTE: 날짜 기반 필터링은 이미 사전 필터링 단계에서 수행됨 (라인 805-858)
            
            # 재조율 요청 여부 판별
            is_reschedule = bool(place_pref.get("rescheduleRequestedBy")) if isinstance(place_pref, dict) else False
            reschedule_requested_at = place_pref.get("rescheduleRequestedAt") if isinstance(place_pref, dict) else None

            requests.append({
                "id": session.get("id"),
                "thread_id": thread_id or session.get("id"),
                "title": summary or f"{initiator_name}님의 일정 요청",
                "summary": summary,
                "initiator_id": initiator_id,
                "initiator_name": initiator_name,
                "initiator_avatar": initiator_avatar,
                "participant_count": participant_count,
                "proposed_date": proposed_date,
                "proposed_time": proposed_time,
                "status": session.get("status"),
                "created_at": session.get("created_at"),
                "reschedule_requested_at": reschedule_requested_at,
                "type": "reschedule" if is_reschedule else "new",
                # [NEW] Frontend 'details.attendees' 접근을 위한 구조 추가
                "details": {
                    "attendees": attendees,
                    "rescheduleRequestedBy": place_pref.get("rescheduleRequestedBy") if isinstance(place_pref, dict) else None
                }
            })
        
        # 최신순 정렬
        requests.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return {"requests": requests}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"요청 목록 조회 실패: {str(e)}")

@router.delete("/session/{session_id}", summary="A2A 세션 삭제 (내 화면에서 숨기기)")
async def delete_a2a_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    A2A 세션을 내 화면에서 숨깁니다.
    - 실제로 DB에서 삭제하지 않고, hidden_by에 추가하여 내 화면에서만 숨김 처리
    - 다른 참여자들은 여전히 해당 세션 및 참여자 목록을 볼 수 있음
    - left_participants와 다름: hidden_by는 참여자 표시에 영향을 주지 않음
    """
    try:
        import json
        from datetime import datetime
        from config.database import supabase
        
        # 세션 존재 및 권한 확인
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 권한 확인 (세션 참여자만 숨김 가능)
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="세션 숨김 권한이 없습니다.")
        
        # place_pref에서 hidden_by 업데이트 (left_participants 대신)
        place_pref = session.get("place_pref", {})
        if isinstance(place_pref, str):
            try:
                place_pref = json.loads(place_pref)
            except:
                place_pref = {}
        
        # hidden_by: 세션을 숨긴 사용자 목록 (참여자 표시에 영향 없음)
        hidden_by = place_pref.get("hidden_by", [])
        if current_user_id not in hidden_by:
            hidden_by.append(current_user_id)
        
        place_pref["hidden_by"] = hidden_by
        
        # DB 업데이트 (삭제 대신 숨김 처리)
        supabase.table('a2a_session').update({
            "place_pref": place_pref,
            "updated_at": datetime.utcnow().isoformat()
        }).eq('id', session_id).execute()
        
        return {"status": "success", "message": "내 화면에서 일정이 숨겨졌습니다."}
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세션 숨김 실패: {str(e)}")

@router.delete("/room/{room_id}", summary="채팅방(스레드 또는 세션) 삭제")
async def delete_chat_room(
        room_id: str,
        current_user_id: str = Depends(get_current_user_id)
):
    """
    채팅방을 삭제합니다.
    - ID가 Thread ID라면 연결된 모든 그룹 세션을 삭제합니다.
    - ID가 Session ID라면 해당 1:1 세션을 삭제합니다.
    """
    try:
        # 삭제 권한 체크 로직을 추가할 수 있으나,
        # Repository 레벨에서 본인 관련 데이터만 지우도록 하거나
        # 현재는 편의상 조회 없이 삭제 시도 (존재하지 않으면 무시됨)

        deleted = await A2ARepository.delete_room(room_id)

        if deleted:
            return {"status": "success", "message": "채팅방이 삭제되었습니다."}
        else:
            raise HTTPException(status_code=500, detail="채팅방 삭제 실패")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅방 삭제 오류: {str(e)}")

@router.post("/session/{session_id}/approve", summary="A2A 세션 일정 승인")
async def approve_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    제안된 일정을 승인합니다.
    - 캘린더에 일정 등록
    - 세션 상태를 completed로 변경
    - 참여자들에게 알림 전송
    """
    try:
        # 권한 확인 및 세션 조회
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="승인 권한이 없습니다.")

        # 승인 로직 실행 (Service에 위임)
        result = await A2AService.approve_session(session_id, current_user_id)
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"일정 승인 실패: {str(e)}")

@router.post("/session/{session_id}/reschedule", summary="A2A 세션 재조율 요청")
async def reschedule_session(
    session_id: str,
    request: Request,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    일정 재조율을 요청합니다.
    - 새로운 요구사항(reason, preferred_time 등)을 반영하여 협상 재개
    """
    try:
        print(f"📥 [Reschedule Router] Incoming request for session: {session_id}")
        body = await request.json()
        print(f"📥 [Reschedule Router] Body: {body}")
        reason = body.get("reason")
        preferred_time = body.get("preferred_time")
        manual_input = body.get("manual_input") or body.get("note")
        new_date = body.get("date")  # 새로 선택한 시작 날짜
        new_time = body.get("time")  # 새로 선택한 시작 시간
        end_date = body.get("endDate")  # 종료 날짜
        end_time = body.get("endTime")  # 종료 시간

        # 권한 확인 및 세션 조회
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="재조율 권한이 없습니다.")

        # 재조율 로직 실행 (Service에 위임)
        result = await A2AService.reschedule_session(
            session_id=session_id,
            user_id=current_user_id,
            reason=reason,
            preferred_time=preferred_time,
            manual_input=manual_input,
            new_date=new_date,
            new_time=new_time,
            end_date=end_date,
            end_time=end_time
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"재조율 요청 실패: {str(e)}")
@router.get("/session/{session_id}/availability", summary="특정 월의 가용 날짜 조회")
async def get_session_availability(
    session_id: str,
    year: int,
    month: int,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    세션 참여자 모두가 가능한 날짜 목록을 반환합니다.
    - year, month 쿼리 파라미터 필요
    """
    try:
        # 권한 확인 (세션 참여자만)
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

        result = await A2AService.get_available_dates(session_id, year, month)
        if result["status"] == 200:
            return result
        else:
            raise HTTPException(status_code=result["status"], detail=result.get("error"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"가용 날짜 조회 실패: {str(e)}")


# ============================================================================
# True A2A: Real-time Negotiation Endpoints
# ============================================================================

@router.post("/session/start-true-a2a", summary="True A2A 세션 시작 (실시간 협상)")
async def start_true_a2a_session(
    request: A2ASessionCreate,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    True A2A 세션을 시작합니다.
    - 세션 생성 후 세션 ID 반환
    - 실시간 협상은 별도 SSE 엔드포인트로 진행
    """
    try:
        # 세션 생성
        session = await A2ARepository.create_session(
            initiator_user_id=current_user_id,
            target_user_id=request.target_user_id,
            intent="schedule",
            place_pref={
                "summary": request.summary,
                "activity": request.summary,
                "location": request.place_pref.get("location") if request.place_pref else None,
                "date": request.time_window.get("date") if request.time_window else None,
                "time": request.time_window.get("time") if request.time_window else None
            } if request.summary else None,
            participant_user_ids=[current_user_id, request.target_user_id]  # 다중 참여자 지원
        )
        
        return {
            "status": 200,
            "session_id": session["id"],
            "message": "세션이 생성되었습니다. SSE 스트림에 연결하여 협상을 시작하세요."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세션 생성 실패: {str(e)}")


@router.get("/session/{session_id}/negotiate/stream", summary="실시간 A2A 협상 스트림")
async def stream_negotiation(
    session_id: str,
    request: Request,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    실시간 A2A 협상을 SSE 스트림으로 제공합니다.
    - 에이전트 간 대화가 실시간으로 전송됩니다.
    - 최대 5라운드까지 협상합니다.
    - 합의 또는 사용자 개입 필요 시 스트림이 종료됩니다.
    """
    # 세션 조회 및 권한 확인
    session = await A2ARepository.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    
    initiator_id = session["initiator_user_id"]
    target_id = session["target_user_id"]
    
    if current_user_id != initiator_id and current_user_id != target_id:
        raise HTTPException(status_code=403, detail="세션 접근 권한이 없습니다.")
    
    # 참여자 목록 구성
    place_pref = session.get("place_pref", {}) or {}
    if isinstance(place_pref, str):
        try:
            place_pref = json.loads(place_pref)
        except:
            place_pref = {}
    
    participant_ids = [target_id]
    
    # 추가 참여자가 있으면 포함
    if place_pref.get("participants"):
        for p in place_pref["participants"]:
            if p != initiator_id and p not in participant_ids:
                participant_ids.append(p)
    
    async def event_generator():
        """SSE 이벤트 생성기"""
        try:
            # NegotiationEngine 초기화
            engine = NegotiationEngine(
                session_id=session_id,
                initiator_user_id=initiator_id,
                participant_user_ids=participant_ids,
                activity=place_pref.get("activity") or place_pref.get("summary"),
                location=place_pref.get("location"),
                target_date=place_pref.get("date"),
                target_time=place_pref.get("time")
            )
            
            # 협상 시작 알림
            yield f"data: {json.dumps({'type': 'START', 'message': '🤖 AI 에이전트들이 협상을 시작합니다...'})}\n\n"
            
            # 협상 진행 (각 메시지를 실시간으로 전송)
            async for message in engine.run_negotiation():
                yield f"data: {json.dumps(message.to_sse_data())}\n\n"
                await asyncio.sleep(0.1)  # SSE 버퍼링 방지
            
            # 협상 결과
            result = engine.get_result()
            yield f"data: {json.dumps({'type': 'END', 'status': result.status.value, 'total_rounds': result.total_rounds})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'ERROR', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/session/{session_id}/human-decision", summary="사용자 최종 결정")
async def submit_human_decision(
    session_id: str,
    request: Request,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    AI 협상 실패 시 사용자가 최종 결정을 내립니다.
    - approved: true면 마지막 제안으로 확정
    - approved: false + counter_proposal이면 새로운 제안으로 재협상
    """
    try:
        body = await request.json()
        approved = body.get("approved", False)
        counter_proposal = body.get("counter_proposal")  # {date, time, location}
        
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        if current_user_id != session["initiator_user_id"] and current_user_id != session["target_user_id"]:
            raise HTTPException(status_code=403, detail="결정 권한이 없습니다.")
        
        if approved:
            # 마지막 제안으로 확정
            result = await A2AService.approve_session(session_id, current_user_id)
            return result
        elif counter_proposal:
            # 새로운 제안으로 재협상
            result = await A2AService.reschedule_session(
                session_id=session_id,
                user_id=current_user_id,
                reason="사용자 직접 결정",
                new_date=counter_proposal.get("date"),
                new_time=counter_proposal.get("time")
            )
            return result
        else:
            raise HTTPException(status_code=400, detail="approved 또는 counter_proposal이 필요합니다.")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"결정 처리 실패: {str(e)}")


@router.post("/session/{session_id}/conflict-choice", summary="충돌 선택 응답")
async def submit_conflict_choice(
    session_id: str,
    request: Request,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    일정 충돌 시 사용자 선택 처리
    - choice: "skip" (참석 불가) | "adjust" (일정 조정 가능)
    """
    try:
        body = await request.json()
        choice = body.get("choice")  # "skip" | "adjust"
        
        if choice not in ["skip", "adjust"]:
            raise HTTPException(status_code=400, detail="choice는 'skip' 또는 'adjust'여야 합니다.")
        
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 참여자 확인
        participant_ids = session.get("participant_user_ids") or []
        if current_user_id != session["initiator_user_id"] and \
           current_user_id != session["target_user_id"] and \
           current_user_id not in participant_ids:
            raise HTTPException(status_code=403, detail="세션 참여자가 아닙니다.")
        
        user = await AuthRepository.find_user_by_id(current_user_id)
        user_name = user.get("name", "사용자") if user else "사용자"
        
        if choice == "skip":
            # 참석 불가 - 세션에서 제외
            place_pref = session.get("place_pref") or {}
            left_participants = place_pref.get("left_participants") or []
            if current_user_id not in left_participants:
                left_participants.append(current_user_id)
                place_pref["left_participants"] = left_participants
                
                # 세션 업데이트 - 협상 재개 상태로
                await A2ARepository.update_session_status(
                    session_id, 
                    "in_progress",  # 협상 재개
                    details={
                        "left_participants": left_participants,
                        "skip_user_id": current_user_id,
                        "skip_user_name": user_name
                    }
                )
            
            # 제외된 사용자 수 확인 - 남은 참여자로 진행 가능 여부 판단
            participant_ids = session.get("participant_user_ids") or []
            all_user_ids = [session["initiator_user_id"]] + participant_ids
            if session.get("target_user_id"):
                all_user_ids.append(session["target_user_id"])
            
            remaining_count = len([uid for uid in all_user_ids if uid not in left_participants])
            
            # 메시지 추가
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=current_user_id,
                receiver_user_id=session["initiator_user_id"],
                message_type="system",
                message={
                    "type": "participant_left",
                    "text": f"{user_name}님이 참석 불가를 선택했습니다. 남은 {remaining_count}명으로 일정을 진행합니다."
                }
            )

            # [NEW] 일정 거절(스킵) 알림 로그 추가 (주최자에게 알림)
            try:
                initiator_id = session.get("initiator_user_id")
                # 거절한 사람이 주최자가 아닌 경우에만 알림
                if initiator_id != current_user_id:
                     # 세션 정보에서 날짜/시간 가져오기
                    place_pref = session.get("place_pref") or {}
                    if isinstance(place_pref, str):
                        try:
                            import json
                            place_pref = json.loads(place_pref)
                        except:
                            place_pref = {}
                            
                    req_date = place_pref.get("date") or place_pref.get("proposedDate")
                    req_time = place_pref.get("time") or place_pref.get("proposedTime")
                    activity = place_pref.get("activity")

                    await A2ARepository.create_chat_log(
                        user_id=initiator_id,  # 알림 받을 사람 (주최자)
                        friend_id=current_user_id,  # 거절한 사람
                        message=f"{user_name}님이 일정을 거절했습니다.",
                        sender="system",
                        message_type="schedule_rejection",
                        metadata={
                            "session_id": session_id,
                            "rejected_by": current_user_id,
                            "rejected_by_name": user_name,
                            "schedule_date": req_date,
                            "schedule_time": req_time,
                            "schedule_activity": activity
                        }
                    )
            except Exception as log_err:
                print(f"거절 알림 로그 생성 실패: {log_err}")
            
            return {
                "status": 200,
                "message": f"참석 불가로 처리되었습니다. 남은 {remaining_count}명이 협상을 계속합니다.",
                "choice": "skip",
                "remaining_count": remaining_count
            }
            
        elif choice == "adjust":
            # 일정 조정 가능 - 협상 계속
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=current_user_id,
                receiver_user_id=session["initiator_user_id"],
                message_type="system",
                message={
                    "type": "adjust_schedule",
                    "text": f"{user_name}님이 일정 조정을 선택했습니다."
                }
            )
            
            return {
                "status": 200,
                "message": "일정 조정이 선택되었습니다. 캐린더에서 일정을 수정해주세요.",
                "choice": "adjust"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"충돌 선택 처리 실패: {str(e)}")


@router.post("/session/{session_id}/send-conflict-notification", summary="충돌 알림 메시지 전송")
async def send_conflict_notification(
    session_id: str,
    request: Request,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    충돌하는 참여자에게 선택지 알림 메시지 전송
    """
    try:
        body = await request.json()
        target_user_id = body.get("target_user_id")
        conflict_event_name = body.get("conflict_event_name", "일정")
        proposed_date = body.get("proposed_date")
        proposed_time = body.get("proposed_time")
        
        if not target_user_id:
            raise HTTPException(status_code=400, detail="target_user_id가 필요합니다.")
        
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 초대자 이름 가져오기
        initiator = await AuthRepository.find_user_by_id(session["initiator_user_id"])
        initiator_name = initiator.get("name", "사용자") if initiator else "사용자"
        
        # 참여자 수 계산
        participant_ids = session.get("participant_user_ids") or []
        participant_count = len(participant_ids) + 1  # +1 for initiator
        
        other_count = participant_count - 1  # target 제외
        
        # 알림 메시지 생성
        notification_message = {
            "type": "schedule_conflict_choice",
            "session_id": session_id,
            "initiator_name": initiator_name,
            "other_count": other_count,
            "proposed_date": proposed_date,
            "proposed_time": proposed_time,
            "conflict_event_name": conflict_event_name,
            "text": f"🔔 {initiator_name}님 외 {other_count}명이 {proposed_date} {proposed_time}에 일정을 잡으려 합니다. 그 시간에 [{conflict_event_name}]이 있으시네요.",
            "choices": [
                {"id": "skip", "label": "참석 불가"},
                {"id": "adjust", "label": "일정 조정 가능"}
            ]
        }
        
        # 대상 사용자의 채팅 세션에 알림 추가
        # origin_chat_session_id를 사용하여 해당 사용자의 채팅에 메시지 전송
        origin_session_id = session.get("origin_chat_session_id")
        
        if origin_session_id:
            # 대상 사용자의 채팅 세션 찾기 (또는 새로 생성)
            await ChatRepository.add_message(
                session_id=origin_session_id,
                user_message=None,
                ai_response=json.dumps(notification_message, ensure_ascii=False),
                intent="a2a_conflict_notification"
            )
        
        return {
            "status": 200,
            "message": "충돌 알림이 전송되었습니다.",
            "notification": notification_message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"알림 전송 실패: {str(e)}")
