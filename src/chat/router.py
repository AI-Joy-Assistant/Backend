from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import jwt
from config.settings import settings
from .service import ChatService
from .models import SendMessageRequest, ChatRoomListResponse, ChatMessagesResponse

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
    """사용자가 AI에게 일정 조율을 요청합니다."""
    result = await ChatService.start_ai_conversation(current_user_id, message)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

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