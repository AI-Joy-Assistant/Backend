from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import jwt
import logging
from config.settings import settings
from config.database import supabase, get_async_supabase
from .chat_service import ChatService
from .chat_models import SendMessageRequest, ChatRoomListResponse, ChatMessagesResponse, AIChatRequest
from .chat_repository import ChatRepository

logger = logging.getLogger(__name__)

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
    request: AIChatRequest,
    current_user_id: str = Depends(get_current_user_id)
):
    """ChatGPT와 자유로운 대화를 합니다."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"DEBUG: /chat Request - title: '{request.title}', location: '{request.location}'")
    
    # AIChatRequest 모델 사용
    
    if not request.message:
        raise HTTPException(status_code=400, detail="메시지가 필요합니다.")
    
    result = await ChatService.start_ai_conversation(
        current_user_id, 
        request.message, 
        selected_friend_ids=request.selected_friends,
        session_id=request.session_id,
        explicit_title=request.title,  # 제목 전달
        explicit_location=request.location,  # 장소 전달
        duration_nights=request.duration_nights,  # 박 수 전달
        start_date=request.start_date,
        end_date=request.end_date,
        start_time=request.start_time,
        end_time=request.end_time,
        duration_minutes=request.duration_minutes,
        is_all_day=request.is_all_day  # ✅ 종일 여부 전달
    )
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/history", summary="채팅 기록 조회")
async def get_chat_history(
    session_id: Optional[str] = None,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    사용자의 채팅 기록을 조회합니다.
    - session_id가 넘어오면 해당 세션의 기록만 조회
    - 없으면 기존처럼 전체 최근 기록 조회
    """
    try:
        chat_logs = await ChatRepository.get_recent_chat_logs(
            current_user_id,
            limit=50,
            session_id=session_id,   # ✅ 추가
        )
        return chat_logs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅 기록 조회 실패: {str(e)}")





@router.get("/default-session", summary="기본 채팅 세션 조회/생성")
async def get_or_create_default_session(
    current_user_id: str = Depends(get_current_user_id)
):
    """
    사용자의 기본 채팅 세션을 조회하거나 없으면 생성합니다.
    [최적화] Async Supabase + 백그라운드 마이그레이션
    """
    import asyncio
    
    try:
        client = await get_async_supabase()
        
        # 1. "기본 채팅" 제목의 세션이 있는지 확인
        existing = await client.table("chat_sessions").select("id, title, created_at, updated_at").eq(
            "user_id", current_user_id
        ).eq("title", "기본 채팅").limit(1).execute()
        
        if existing.data and len(existing.data) > 0:
            session = existing.data[0]
            return {
                "id": session["id"],
                "title": session.get("title", "기본 채팅"),
                "created_at": session.get("created_at"),
                "updated_at": session.get("updated_at"),
                "is_new": False
            }
        
        # 2. 기본 채팅 세션 생성
        new_session = await client.table("chat_sessions").insert({
            "user_id": current_user_id,
            "title": "기본 채팅",
        }).execute()
        
        if not new_session.data:
            raise HTTPException(status_code=500, detail="기본 채팅 세션 생성 실패")
        
        session = new_session.data[0]
        session_id = session["id"]
        
        # 3. 마이그레이션을 백그라운드 태스크로 실행
        async def migrate_old_messages():
            try:
                bg_client = await get_async_supabase()
                await bg_client.table("chat_log").update({
                    "session_id": session_id
                }).eq("user_id", current_user_id).is_("session_id", "null").execute()
            except Exception as e:
                logger.warning(f"메시지 마이그레이션 실패: {e}")
        
        asyncio.create_task(migrate_old_messages())
        
        return {
            "id": session_id,
            "title": session.get("title", "기본 채팅"),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "is_new": True
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"기본 채팅 세션 조회/생성 실패: {str(e)}")


@router.get("/sessions", summary="채팅 세션 목록 조회")
async def get_chat_sessions(
    current_user_id: str = Depends(get_current_user_id)
):
    """사용자의 채팅 세션 목록을 조회합니다."""
    try:
        client = await get_async_supabase()
        res = await client.table("chat_sessions").select("id, title, created_at, updated_at").eq(
            "user_id", current_user_id
        ).order("updated_at", desc=True).execute()
        
        sessions = []
        for session in (res.data or []):
            sessions.append({
                "id": session["id"],
                "title": session.get("title", "새 채팅"),
                "created_at": session.get("created_at"),
                "updated_at": session.get("updated_at"),
                "is_default": session.get("title") == "기본 채팅",
            })
        
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅 세션 목록 조회 실패: {str(e)}")

@router.post("/sessions", summary="새 채팅 세션 생성")
async def create_chat_session(
    current_user_id: str = Depends(get_current_user_id)
):
    """
    비서 채팅 화면에서 사용할 '채팅 세션(채팅방)'을 하나 생성합니다.
    chat_sessions 테이블에 row를 하나 만들고, 생성된 정보를 그대로 돌려줍니다.
    """
    try:
        res = supabase.table("chat_sessions").insert({
            "user_id": current_user_id,
            "title": "새 채팅",
        }).execute()

        if not res.data:
            raise HTTPException(status_code=500, detail="채팅 세션 생성 실패")

        session = res.data[0]

        return {
            "id": session["id"],
            "title": session.get("title", "새 채팅"),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅 세션 생성 실패: {str(e)}")


@router.delete("/sessions/{session_id}", summary="채팅 세션 삭제")
async def delete_chat_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    채팅 세션과 관련 메시지를 삭제합니다.
    """
    try:
        # 세션이 현재 사용자의 것인지 확인
        check = supabase.table("chat_sessions").select("id").eq(
            "id", session_id
        ).eq("user_id", current_user_id).execute()
        
        if not check.data:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 관련 채팅 로그 삭제
        supabase.table("chat_log").delete().eq("session_id", session_id).execute()
        
        # 세션 삭제
        supabase.table("chat_sessions").delete().eq("id", session_id).execute()
        
        return {"status": "ok", "message": "세션이 삭제되었습니다."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅 세션 삭제 실패: {str(e)}")


@router.put("/sessions/{session_id}", summary="채팅 세션 이름 변경")
async def update_chat_session(
    session_id: str,
    request: dict,
    current_user_id: str = Depends(get_current_user_id)
):
    """
    채팅 세션의 제목을 변경합니다.
    """
    try:
        print(f"DEBUG: Processing PUT /sessions/{session_id}")
        title = request.get("title", "").strip()
        print(f"DEBUG: New title requested: {title}")
        if not title:
            raise HTTPException(status_code=400, detail="제목을 입력해주세요.")
        
        # 세션이 현재 사용자의 것인지 확인
        check = supabase.table("chat_sessions").select("id").eq(
            "id", session_id
        ).eq("user_id", current_user_id).execute()
        
        if not check.data:
            print(f"DEBUG: Session {session_id} not found for user {current_user_id}")
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        
        # 세션 제목 업데이트
        result = supabase.table("chat_sessions").update({
            "title": title
        }).eq("id", session_id).execute()
        print(f"DEBUG: Update result data: {result.data}")
        
        return {"status": "ok", "message": "세션 이름이 변경되었습니다.", "title": title}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"채팅 세션 이름 변경 실패: {str(e)}")

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
        from src.a2a.a2a_service import A2AService
        from src.chat.chat_repository import ChatRepository
        
        thread_id = request.get("thread_id")  # thread_id는 Optional (1:1 세션은 없을 수 있음)
        session_ids = request.get("session_ids", [])
        approved = request.get("approved", False)
        proposal = request.get("proposal")
        
        if not proposal:
            raise HTTPException(status_code=400, detail="proposal이 필요합니다.")
        
        if not thread_id and not session_ids:
            raise HTTPException(status_code=400, detail="thread_id 또는 session_ids가 필요합니다.")
        
        # 사용자의 승인/거절 의사를 chat_log에 저장
        # user_response_text = "예" if approved else "아니오"
        try:
            await ChatRepository.create_chat_log(
                user_id=current_user_id,
                request_text=None,  # [✅ 수정] "예" 대신 None으로 설정
                response_text=None,
                friend_id=None,
                message_type="approval_response", # 타입은 유지 (로직 처리를 위해)
                metadata={
                    "approved": approved,
                    "thread_id": thread_id,
                    "session_ids": session_ids,
                    "proposal": proposal
                }
            )
        except Exception as e:
            # metadata 저장 실패해도 계속 진행 (로깅만)
            logger.warning(f"승인/거절 의사 저장 실패: {str(e)}")
        
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


@router.get("/notifications", summary="알림 조회")
async def get_notifications(
    current_user_id: str = Depends(get_current_user_id)
):
    """
    사용자의 알림을 조회합니다.
    - 친구 요청 알림 (friend_request)
    - 일정 거절 알림 (schedule_rejected)
    - 시스템 알림 등
    """
    try:
        notifications = []
        
        # 1. 친구 요청 알림 조회 (friend_follow 테이블)
        try:
            friend_requests = supabase.table("friend_follow").select(
                "*, request_user:user!friend_follow_request_id_fkey(name, profile_image)"
            ).eq("receiver_id", current_user_id).eq("follow_status", "pending").order(
                "requested_at", desc=True
            ).limit(20).execute()
            
            for req in (friend_requests.data or []):
                request_user = req.get("request_user", {}) or {}
                from_user_name = request_user.get("name", "알 수 없음")
                
                notifications.append({
                    "id": req.get("id"),
                    "type": "friend_request",
                    "title": "친구 요청",
                    "message": f"{from_user_name}님이 친구 요청을 보냈습니다.",
                    "created_at": req.get("requested_at"),
                    "read": False,
                    "metadata": {
                        "from_user_id": req.get("request_id"),
                        "from_user_name": from_user_name,
                        "from_user_avatar": request_user.get("profile_image")
                    }
                })
        except Exception as friend_error:
            logger.warning(f"친구 요청 알림 조회 실패: {friend_error}")
        
        # 2. 알림 로그 조회 (일정 거절, 친구 수락/거절, 일정 확정)
        notification_types = ["schedule_rejection", "friend_accepted", "friend_rejected", "schedule_confirmed"]
        logs = supabase.table("chat_log").select("*").eq(
            "user_id", current_user_id
        ).in_("message_type", notification_types).order(
            "created_at", desc=True
        ).limit(30).execute()
        
        # [최적화] 모든 user_id를 먼저 수집하여 배치 조회
        all_user_ids = set()
        for log in (logs.data or []):
            metadata = log.get("metadata", {}) or {}
            friend_id = log.get("friend_id")
            target_user_id = friend_id or metadata.get("rejected_by") or metadata.get("left_user_id")
            if target_user_id:
                all_user_ids.add(target_user_id)
        
        # [최적화] 한 번의 쿼리로 모든 사용자 이름 조회
        user_name_map = {}
        if all_user_ids:
            try:
                users_res = supabase.table("user").select("id, name").in_("id", list(all_user_ids)).execute()
                user_name_map = {u["id"]: u.get("name", "상대방") for u in (users_res.data or [])}
            except Exception as e:
                logger.warning(f"사용자 이름 배치 조회 실패: {e}")
        
        for log in (logs.data or []):
            msg_type = log.get("message_type")
            metadata = log.get("metadata", {}) or {}
            friend_id = log.get("friend_id")
            
            # [최적화] 맵에서 조회 (DB 호출 없음)
            target_user_id = friend_id or metadata.get("rejected_by") or metadata.get("left_user_id")
            target_user_name = user_name_map.get(target_user_id, "상대방") if target_user_id else "상대방"
            
            if msg_type == "schedule_rejection":
                # 일정 거절 메시지 구성
                schedule_date = metadata.get("schedule_date", "")
                schedule_time = metadata.get("schedule_time", "")
                schedule_activity = metadata.get("schedule_activity", "")
                
                schedule_info = ""
                if schedule_date or schedule_time:
                    schedule_info = f"{schedule_date} {schedule_time}".strip()
                if schedule_activity:
                    schedule_info = f"'{schedule_activity}' ({schedule_info})" if schedule_info else f"'{schedule_activity}'"
                
                if schedule_info:
                    message = f"{target_user_name}님이 {schedule_info} 일정을 거절했습니다."
                else:
                    message = f"{target_user_name}님이 일정을 거절했습니다."
                
                title = "일정 거절"
                
            elif msg_type == "friend_accepted":
                title = "친구 수락"
                message = f"{target_user_name}님이 친구 요청을 수락했습니다. 이제 일정을 조율해보세요!"
            
            elif msg_type == "schedule_confirmed":
                # 일정 확정 메시지 구성
                confirmed_date = metadata.get("confirmed_date", "")
                confirmed_time = metadata.get("confirmed_time", "")
                activity = metadata.get("activity", "일정")
                others = metadata.get("others", [])
                
                title = "일정 확정"
                
                time_str = f"{confirmed_date} {confirmed_time}".strip()
                if others:
                    others_str = ", ".join(others)
                    message = f"{others_str}님과의 '{activity}' 일정이 {time_str}에 확정되었습니다."
                else:
                    message = f"'{activity}' 일정이 {time_str}에 확정되었습니다."
                
            elif msg_type == "friend_rejected":
                title = "친구 거절"
                message = f"{target_user_name}님이 친구 요청을 거절했습니다."
            else:
                continue

            notifications.append({
                "id": log.get("id"),
                "type": msg_type,
                "title": title,
                "message": message,
                "created_at": log.get("created_at"),
                "read": False,
                "metadata": metadata
            })
        
        # 최신순 정렬
        notifications.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        return {"notifications": notifications}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"알림 조회 실패: {str(e)}")