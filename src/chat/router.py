from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import jwt
from config.settings import settings
from .service import ChatService
from .models import SendMessageRequest, ChatRoomListResponse, ChatMessagesResponse
from .repository import ChatRepository

router = APIRouter(prefix="/chat", tags=["Chat"])

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

@router.get("/rooms", summary="채팅방 목록 조회")
async def get_chat_rooms(current_user_id: str = Depends(get_current_user_id)):
    """사용자의 채팅방 목록을 조회합니다."""
    result = await ChatService.get_chat_rooms(current_user_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/test-rooms", summary="채팅방 목록 테스트 (인증 없음)")
async def test_chat_rooms():
    """인증 없이 채팅방 목록을 테스트합니다."""
    # 고정 사용자 ID로 테스트
    result = await ChatService.get_chat_rooms("test-user-id")
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/messages/{other_user_id}", summary="채팅 메시지 조회")
async def get_chat_messages(
    other_user_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """특정 사용자와의 채팅 메시지를 조회합니다."""
    result = await ChatService.get_chat_messages(current_user_id, other_user_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.post("/send", summary="메시지 전송")
async def send_message(
    message_request: SendMessageRequest,
    current_user_id: str = Depends(get_current_user_id)
):
    """메시지를 전송합니다."""
    result = await ChatService.send_message(
        send_id=current_user_id,
        receive_id=str(message_request.receive_id),
        message=message_request.message,
        message_type=message_request.message_type
    )
    
    if result["status"] == 200:
        return {
            "message": result["message"],
            "data": result["data"]
        }
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/friends", summary="친구 목록 조회")
async def get_friends(current_user_id: str = Depends(get_current_user_id)):
    """사용자의 친구 목록을 조회합니다."""
    result = await ChatService.get_friends(current_user_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.post("/start-ai-session", summary="AI와 일정 조율 대화 시작")
async def start_ai_conversation(
    message: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """사용자가 AI에게 일정 조율을 요청합니다. (ChatGPT API 사용)"""
    result = await ChatService.start_ai_conversation(current_user_id, message)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.post("/chat", summary="ChatGPT와 대화")
async def chat_with_gpt(
    request: dict,
    current_user_id: str = Depends(get_current_user_id)
):
    """ChatGPT와 자유로운 대화를 합니다."""
    message = request.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="메시지가 필요합니다.")
    
    result = await ChatService.start_ai_conversation(current_user_id, message)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/history", summary="채팅 기록 조회")
async def get_chat_history(
    current_user_id: str = Depends(get_current_user_id)
):
    """사용자의 채팅 기록을 조회합니다."""
    try:
        chat_logs = await ChatRepository.get_recent_chat_logs(current_user_id, limit=50)
        return chat_logs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅 기록 조회 실패: {str(e)}")

@router.get("/friend/{friend_id}", summary="특정 친구와의 대화 내용 조회")
async def get_friend_messages(
    friend_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """특정 친구와의 모든 대화 내용을 조회합니다."""
    result = await ChatService.get_friend_conversation(current_user_id, friend_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"]) 

@router.post("/log", summary="A2A 등 외부 흐름에서 대화 로그 남기기")
async def append_chat_log(
    request: dict,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    외부(예: A2A 자동 조율)에서 상단 히스토리에 보여줄 로그를 직접 남깁니다.
    body: { friend_id: string, message: string, role: 'user'|'ai'|'system' }
    """
    friend_id = request.get("friend_id")
    message = request.get("message")
    role = (request.get("role") or "ai").lower()

    if not friend_id or not message:
        raise HTTPException(status_code=400, detail="friend_id와 message가 필요합니다.")

    try:
        if role == "user":
            await ChatRepository.create_chat_log(
                user_id=current_user_id,
                request_text=message,
                response_text=None,
                friend_id=friend_id,
                message_type="user_message",
            )
        else:
            await ChatRepository.create_chat_log(
                user_id=current_user_id,
                request_text=None,
                response_text=message,
                friend_id=friend_id,
                message_type="ai_response",
            )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"로그 기록 실패: {str(e)}")

@router.delete("/rooms/{friend_id}", summary="채팅방(대화 히스토리) 삭제")
async def delete_chat_room(friend_id: str, current_user_id: str = Depends(get_current_user_id)):
    """
    현재 사용자 기준으로 특정 친구와의 방(히스토리)을 삭제합니다.
    chat_log에서 user_id, friend_id로 매칭되는 레코드를 제거합니다.
    """
    try:
        deleted = await ChatRepository.delete_chat_room(current_user_id, friend_id)
        return {"deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"삭제 실패: {str(e)}")

@router.post("/approve-schedule", summary="일정 승인/거절")
async def approve_schedule(
    request: dict,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    일정 확정 제안에 대한 승인/거절 처리
    body: {
        "thread_id": "string",
        "session_ids": ["string"],
        "approved": true/false,
        "proposal": {
            "date": "string",
            "time": "string",
            "location": "string",
            "participants": ["string"]
        }
    }
    """
    try:
        from src.a2a.service import A2AService
        from src.chat.repository import ChatRepository
        
        thread_id = request.get("thread_id")
        session_ids = request.get("session_ids", [])
        approved = request.get("approved", False)
        proposal = request.get("proposal")
        
        if not thread_id or not proposal:
            raise HTTPException(status_code=400, detail="thread_id와 proposal이 필요합니다.")
        
        # 사용자의 승인/거절 의사를 chat_log에 저장
        user_response_text = "예" if approved else "아니오"
        await ChatRepository.create_chat_log(
            user_id=current_user_id,
            request_text=user_response_text,
            response_text=None,
            friend_id=None,
            message_type="schedule_approval_response",
            metadata={
                "approved": approved,
                "thread_id": thread_id,
                "session_ids": session_ids,
                "proposal": proposal
            }
        )
        
        result = await A2AService.handle_schedule_approval(
            thread_id=thread_id,
            session_ids=session_ids,
            user_id=current_user_id,
            approved=approved,
            proposal=proposal
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"일정 승인 처리 실패: {str(e)}")