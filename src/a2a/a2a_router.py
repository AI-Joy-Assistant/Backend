from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional
import jwt
from config.settings import settings
from .a2a_service import A2AService
from .a2a_repository import A2ARepository
from .a2a_models import A2ASessionCreate, A2ASessionResponse, A2AMessageResponse
from src.auth.auth_service import AuthService
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
            duration_minutes=request.time_window.get("duration_minutes", 60) if request.time_window else 60
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
        # 1. 메시지 조회하여 Process 구성
        messages = await A2ARepository.get_session_messages(session_id)
        process = []
        for msg in messages:
            step = msg.get("message", {}).get("step")
            text = msg.get("message", {}).get("text")
            if step and text:
                process.append({"step": str(step), "description": text})
        
        # 2. 기본 정보
        place_pref = session.get("place_pref", {}) or {}
        summary = place_pref.get("summary") or session.get("summary")
        
        # Initiator 이름 (여기서는 간단히 DB 조회 없이 ID로 처리하거나, 필요시 조회)
        # 성능상 이름 조회는 생략하거나 캐시된 정보 사용 권장. 
        # 여기서는 간단히 처리.
        initiator_name = "알 수 없음" # 클라이언트에서 처리하거나 별도 조회 필요
        
        details = {
            "proposer": initiator_name, # 클라이언트에서 목록의 정보를 활용하거나 별도 API로 보완
            "proposerAvatar": "https://via.placeholder.com/150",
            "purpose": summary or "일정 조율",
            "proposedTime": place_pref.get("time") or "미정",
            "location": place_pref.get("location") or "미정",
            "process": process
        }

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

            # 참여자 ID 수집 (initiator + target)
            initiators = {s.get("initiator_user_id") for s in thread_sessions}
            targets = {s.get("target_user_id") for s in thread_sessions}

            # place_pref에 명시된 참여자 정보도 확인
            place_pref = representative.get("place_pref", {})
            pref_participants = set()
            if isinstance(place_pref, dict) and place_pref.get("participants"):
                pref_participants = set(place_pref.get("participants"))

            # 전체 참여자 합집합 (나 제외)
            participants_set = (initiators | targets | pref_participants) - {current_user_id}

            participant_list = list(participants_set)
            all_participant_ids.update(participants_set) # 전체 ID 수집

            # 대표 세션 객체에 정보 주입
            representative["thread_id"] = thread_id
            representative["participant_ids"] = participant_list
            representative["participant_count"] = len(participant_list)
            
            grouped_sessions.append(representative)
        
        # 최근 순으로 정렬
        grouped_sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # 3. 이름 일괄 조회 (DB 부하 감소)
        user_names_map = {}
        if all_participant_ids:
            user_names_map = await ChatRepository.get_user_names_by_ids(list(all_participant_ids))

        # 4. 이름 매핑 적용
        for session in grouped_sessions:
            p_ids = session.get("participant_ids", [])
            p_names = []
            for pid in p_ids:
                name = user_names_map.get(pid, "알 수 없음")
                p_names.append(name)

            # 이름이 없으면(탈퇴 등) '대화상대'로 표시
            if not p_names:
                p_names = ["대화상대"]

            session["participant_names"] = p_names

        # 5. 최신순 정렬
        grouped_sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # 6. 추가 정보(title, details) 구성
        final_sessions = []
        for session in grouped_sessions:
            # 기본 정보
            place_pref = session.get("place_pref", {}) or {}
            summary = place_pref.get("summary") or session.get("summary")
            
            # Title
            p_names = session.get("participant_names", [])
            title = summary if summary else f"{', '.join(p_names)}와의 약속"
            
            # Details 구성
            # Initiator 이름 찾기
            initiator_id = session.get("initiator_user_id")
            initiator_name = "알 수 없음"
            if initiator_id in user_names_map:
                initiator_name = user_names_map[initiator_id]
            
            # Process (간소화: 메시지 수 기반으로 가짜 스텝 생성 혹은 실제 메시지 조회)
            # 리스트 조회 성능을 위해 여기서는 빈 배열 혹은 간단한 정보만 넣고, 
            # 상세 조회 시 채우는 것이 좋으나 UI 요구사항에 맞춰 기본 구조만 잡음
            process = [] 
            
            details = {
                "proposer": initiator_name,
                "proposerAvatar": "https://via.placeholder.com/150", # TODO: 실제 아바타 URL
                "purpose": summary or "일정 조율",
                "proposedTime": place_pref.get("time") or "미정",
                "location": place_pref.get("location") or "미정",
                "process": process
            }

            session["title"] = title
            session["summary"] = summary
            session["details"] = details
            
            final_sessions.append(A2ASessionResponse(**session))

        return {
            "sessions": final_sessions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세션 목록 조회 실패: {str(e)}")

@router.delete("/session/{session_id}", summary="A2A 세션 삭제")
async def delete_a2a_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """A2A 세션 삭제 (세션과 관련된 모든 메시지도 함께 삭제)"""
    try:
        # 세션 존재 및 권한 확인
        session = await A2ARepository.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 권한 확인 (세션 참여자만 삭제 가능)
        if session["initiator_user_id"] != current_user_id and session["target_user_id"] != current_user_id:
            raise HTTPException(status_code=403, detail="세션 삭제 권한이 없습니다.")
        
        # 세션 삭제 (메시지도 함께 삭제)
        deleted = await A2ARepository.delete_session(session_id)
        
        if deleted:
            return {"status": "success", "message": "세션이 삭제되었습니다."}
        else:
            raise HTTPException(status_code=500, detail="세션 삭제 실패")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세션 삭제 실패: {str(e)}")

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
        body = await request.json()
        reason = body.get("reason")
        preferred_time = body.get("preferred_time")
        manual_input = body.get("manual_input")

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
            manual_input=manual_input
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"재조율 요청 실패: {str(e)}")
