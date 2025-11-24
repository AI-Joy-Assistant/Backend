from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional
import jwt
from config.settings import settings
from .service import A2AService
from .repository import A2ARepository
from .models import A2ASessionCreate, A2ASessionResponse, A2AMessageResponse
from src.auth.service import AuthService

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
        for thread_id, thread_sessions in sessions_by_thread.items():
            # 가장 최근 세션을 대표로 사용
            representative = max(thread_sessions, key=lambda x: x.get('created_at', ''))
            
            # 참여자 정보 추가
            participants = []
            for s in thread_sessions:
                if s.get("initiator_user_id") == current_user_id:
                    participants.append(s.get("target_user_id"))
                else:
                    participants.append(s.get("initiator_user_id"))
            
            # 중복 제거
            participants = list(set(participants))
            
            # thread_id와 참여자 정보를 place_pref에 추가
            representative["thread_id"] = thread_id
            representative["participant_count"] = len(participants)
            representative["participant_ids"] = participants
            
            grouped_sessions.append(representative)
        
        # 최근 순으로 정렬
        grouped_sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return {
            "sessions": [A2ASessionResponse(**session) for session in grouped_sessions]
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

