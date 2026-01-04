from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo
from .chat_repository import ChatRepository
from .chat_models import ChatRoom, ChatMessage, ChatRoomListResponse, ChatMessagesResponse
from .chat_openai_service import OpenAIService
from collections import defaultdict
import uuid
import logging
from datetime import datetime, timedelta
import re
from src.intent.service import IntentService
from config.database import supabase
from src.websocket.websocket_manager import manager as ws_manager

logger = logging.getLogger(__name__)


class ChatService:
    
    @staticmethod
    async def send_ws_notification(user_id: str, notification_type: str, data: dict):
        """WebSocket으로 실시간 알림 전송"""
        try:
            message = {
                "type": notification_type,
                "data": data,
                "timestamp": datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
            }
            await ws_manager.send_personal_message(message, user_id)
            logger.info(f"[WS] 알림 전송: {user_id} - {notification_type}")
        except Exception as e:
            logger.warning(f"[WS] 알림 전송 실패: {e}")


    @staticmethod
    async def get_chat_rooms(user_id: str) -> Dict[str, Any]:
        """사용자의 일정 조율 세션(채팅방) 목록 조회"""
        try:
            # chat_log에서 사용자의 세션들 조회
            sessions = await ChatRepository.get_user_chat_sessions(user_id)

            # 친구별로 그룹화
            friend_map = defaultdict(lambda: {
                'friend_id': None,
                'friend_name': None,
                'last_message': None,
                'last_message_time': None
            })

            for session in sessions:
                friend_id = session['friend_id']

                if friend_map[friend_id]['last_message_time'] is None or session['created_at'] > friend_map[friend_id]['last_message_time']:
                    friend_map[friend_id]['friend_id'] = friend_id
                    friend_map[friend_id]['last_message'] = session['response_text'] or session['request_text']
                    friend_map[friend_id]['last_message_time'] = session['created_at']

            # 친구 이름들 조회
            friend_ids = [data['friend_id'] for data in friend_map.values() if data['friend_id']]
            user_names = await ChatRepository.get_user_names_by_ids(friend_ids)

            # ChatRoom 객체로 변환
            chat_rooms = []
            for friend_data in friend_map.values():
                friend_name = user_names.get(friend_data['friend_id'], '알 수 없음')

                chat_room = ChatRoom(
                    participants=[user_id, friend_data['friend_id']],
                    last_message=friend_data['last_message'],
                    last_message_time=friend_data['last_message_time'],
                    participant_names=[friend_name]  # 친구 이름만 표시
                )
                chat_rooms.append(chat_room)

            # 최근 활동 시간순으로 정렬
            chat_rooms.sort(key=lambda x: x.last_message_time or '', reverse=True)

            return {
                "status": 200,
                "data": ChatRoomListResponse(chat_rooms=chat_rooms)
            }

        except Exception as e:
            return {
                "status": 500,
                "error": f"채팅방 목록 조회 실패: {str(e)}"
            }

    @staticmethod
    async def get_chat_messages(user_id: str, other_user_id: str) -> Dict[str, Any]:
        """두 사용자 간의 채팅 메시지 조회 (chat_log 기반)"""
        try:
            messages_data = await ChatRepository.get_chat_messages(user_id, other_user_id)

            messages = []
            for msg in messages_data:
                # chat_log 형식을 ChatMessage 형식으로 변환
                # request_text가 있으면 사용자 메시지, response_text가 있으면 AI 응답
                if msg.get('request_text'):
                    messages.append(ChatMessage(
                        id=msg['id'],
                        send_id=msg['user_id'],
                        receive_id=msg.get('friend_id', other_user_id),
                        message=msg['request_text'],
                        message_type=msg.get('message_type', 'text'),
                        created_at=msg['created_at']
                    ))
                if msg.get('response_text'):
                    messages.append(ChatMessage(
                        id=f"{msg['id']}_response",
                        send_id='ai',  # AI 응답
                        receive_id=msg['user_id'],
                        message=msg['response_text'],
                        message_type='ai_response',
                        created_at=msg['created_at']
                    ))

            return {
                "status": 200,
                "data": ChatMessagesResponse(messages=messages)
            }

        except Exception as e:
            logger.error(f"채팅 메시지 조회 실패: {str(e)}")
            return {
                "status": 500,
                "error": f"채팅 메시지 조회 실패: {str(e)}"
            }

    @staticmethod
    async def send_message(send_id: str, receive_id: str, message: str, message_type: str = "text") -> Dict[str, Any]:
        """메시지 전송"""
        try:
            # 메시지 전송 (chat_log에 한 줄 저장)
            sent_message = await ChatRepository.send_message(send_id, receive_id, message, message_type)

            message_obj = ChatMessage(
                id=sent_message['id'],
                send_id=sent_message.get('user_id'),
                receive_id=sent_message.get('friend_id'),
                message=sent_message.get('request_text'),
                message_type=sent_message.get('message_type', 'text'),
                created_at=sent_message['created_at']
            )

            return {
                "status": 200,
                "data": message_obj,
                "message": "메시지가 성공적으로 전송되었습니다."
            }

        except Exception as e:
            return {
                "status": 500,
                "error": f"메시지 전송 실패: {str(e)}"
            }

    @staticmethod
    async def get_friends(user_id: str) -> Dict[str, Any]:
        """친구 목록 조회"""
        try:
            friends_data = await ChatRepository.get_friends_list(user_id)
            friend_ids = [friend['friend_id'] for friend in friends_data]

            if friend_ids:
                user_names = await ChatRepository.get_user_names_by_ids(friend_ids)
                friends = [
                    {
                        "id": friend_id,
                        "name": user_names.get(friend_id, '이름 없음')
                    }
                    for friend_id in friend_ids
                ]
            else:
                friends = []

            return {
                "status": 200,
                "data": {"friends": friends}
            }

        except Exception as e:
            return {
                "status": 500,
                "error": f"친구 목록 조회 실패: {str(e)}"
            }

    @staticmethod
    async def start_ai_conversation(
        user_id: str,
        message: str,
        selected_friend_ids: Optional[List[str]] = None,
        session_id: Optional[str] = None,   # ✅ 프론트에서 넘어오는 session_id
        explicit_title: Optional[str] = None,  # ✅ 프론트에서 넘어오는 제목
        explicit_location: Optional[str] = None,  # ✅ 프론트에서 넘어오는 장소
        duration_nights: int = 0,  # ✅ 박 수 (0이면 당일)
        explicit_start_time: Optional[str] = None,  # ✅ 프론트에서 넘어오는 시작 시간 (HH:MM)
        explicit_end_time: Optional[str] = None,  # ✅ 프론트에서 넘어오는 종료 시간 (HH:MM)
    ) -> Dict[str, Any]:
        """AI와 일정 조율 대화 시작"""
        try:
            # 1. 사용자 메시지 저장
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=message,
                response_text=None,
                friend_id=None,
                message_type="user_message",
                session_id=session_id,  # ✅ 세션 연결
            )

            # [추가] 세션 제목 자동 업데이트 ("새 채팅"일 경우 첫 메시지로 변경)
            if session_id:
                try:
                    logger.error(f"DEBUG: Session update attempt. Session ID: {session_id}")
                    # 현재 세션 정보 조회
                    current_session = supabase.table("chat_sessions").select("title").eq("id", session_id).single().execute()
                    if current_session.data:
                        current_title = current_session.data.get("title")
                        logger.debug(f"DEBUG: Current Title: {current_title}")
                        if current_title: 
                             if current_title.strip() == "새 채팅":
                                # 메시지가 길면 20자로 자름
                                new_title = message[:20] + "..." if len(message) > 20 else message
                                logger.debug(f"DEBUG: Updating to new title: {new_title}")
                                await ChatRepository.update_session_title(session_id, new_title, user_id)
                             else:
                                 logger.debug(f"DEBUG: Title is not '새 채팅', skipping update. Title is '{current_title}'")
                        else:
                             logger.debug("DEBUG: Title is empty/None")
                    else:
                        logger.debug("DEBUG: Session not found in DB.")
                except Exception as e:
                    logger.error(f"DEBUG: Error updating title: {e}")

            # 2. [✅ NEW] 시간 응답 처리 (date_selected_mode)
            recent_logs = await ChatRepository.get_recent_chat_logs(user_id, limit=3, session_id=session_id)
            
            date_selected_context = None
            for log in recent_logs:
                meta = log.get("metadata") or {}
                if meta.get("date_selected_mode") and meta.get("selected_date"):
                    date_selected_context = meta
                    break
            
            if date_selected_context:
                # [NEW] 끝나는 시간 대기 모드인지 확인
                waiting_for_end_time = date_selected_context.get("waiting_for_end_time")
                
                if waiting_for_end_time:
                    # 끝나는 시간 응답 처리
                    selected_start_time = date_selected_context.get("selected_start_time")
                    selected_date = date_selected_context.get("selected_date")
                    friend_ids = date_selected_context.get("friend_ids", [])
                    activity = date_selected_context.get("activity")
                    location = date_selected_context.get("location")
                    
                    # "몰라", "모르겠어", "없어", "아닝", "글쎄" 등 모르겠다는 응답 감지
                    dont_know_patterns = ["몰라", "모르겠", "없어", "아닝", "글쎄", "미정", "아직", "잘 모", "모름", "아무", "상관없"]
                    is_dont_know = any(p in message for p in dont_know_patterns)
                    
                    if is_dont_know:
                        # 기본값: 24시(자정)까지
                        selected_end_time = "24:00"
                        end_time_msg = f"알겠습니다! 끝나는 시간은 24시(자정)까지로 잡을게요 🌙"
                        # logger.info(f"[End Time] 모르겠다 응답 -> 24:00 기본값 적용")
                    else:
                        # 끝나는 시간 파싱 시도
                        time_match = re.search(r'(\d{1,2})\s*[시:]', message)
                        if time_match:
                            hour = int(time_match.group(1))
                            
                            # 오후/오전 처리
                            if "오후" in message and hour < 12:
                                hour += 12
                            elif "오전" in message and hour == 12:
                                hour = 0
                            elif "오전" not in message and "오후" not in message and hour < 7:
                                hour += 12
                            
                            selected_end_time = f"{hour:02d}:00"
                            end_time_msg = None
                            # logger.info(f"[End Time] 시간 파싱: {message} -> {selected_end_time}")
                        else:
                            # 파싱 실패 시 다시 물어보기
                            retry_msg = "🤔 끝나는 시간을 이해하지 못했어요. 다시 한 번 알려주시겠어요? (예: 8시, 오후 10시) 또는 모르시면 '몰라'라고 해주세요!"
                            await ChatRepository.create_chat_log(
                                user_id=user_id,
                                request_text=None,
                                response_text=retry_msg,
                                friend_id=None,
                                message_type="ai_response",
                                session_id=session_id,
                                metadata=date_selected_context  # 컨텍스트 유지
                            )
                            return {
                                "status": 200,
                                "data": {
                                    "user_message": message,
                                    "ai_response": retry_msg,
                                    "schedule_info": {"waiting_for_end_time": True},
                                    "calendar_event": None,
                                    "usage": None
                                }
                            }
                    
                    # 끝나는 시간 확보됨 → A2A 시작
                    from src.a2a.a2a_service import A2AService
                    
                    # 끝 시간 처리 메시지가 있으면 먼저 전송
                    if end_time_msg:
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=end_time_msg,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,
                        )
                    
                    confirm_msg = f"✅ {selected_date} {selected_start_time}~{selected_end_time}로 상대방에게 요청을 보냈습니다. A2A 화면에서 확인해주세요!"
                    await ChatRepository.create_chat_log(
                        user_id=user_id,
                        request_text=None,
                        response_text=confirm_msg,
                        friend_id=None,
                        message_type="ai_response",
                        session_id=session_id,
                    )
                    
                    # A2A 협상 시작 (시작~끝 시간 전달)
                    # duration_minutes 계산
                    start_hour = int(selected_start_time.split(":")[0]) if selected_start_time and ":" in selected_start_time else 14
                    end_hour = int(selected_end_time.split(":")[0]) if selected_end_time and ":" in selected_end_time else (start_hour + 1)
                    if end_hour == 24:
                        end_hour = 0  # 자정은 다음날 0시로 처리
                    duration_minutes = (end_hour - start_hour) * 60 if end_hour > start_hour else ((24 - start_hour) + end_hour) * 60
                    if duration_minutes <= 0:
                        duration_minutes = 60  # 기본값
                    
                    a2a_result = await A2AService.start_multi_user_session(
                        initiator_user_id=user_id,
                        target_user_ids=friend_ids,
                        summary=activity or "약속",
                        date=selected_date,
                        time=selected_start_time,
                        end_time=selected_end_time,  # [✅ NEW] 끝나는 시간 전달
                        location=location,
                        activity=activity,
                        duration_minutes=duration_minutes,
                        force_new=True,
                        origin_chat_session_id=session_id
                    )
                    
                    return {
                        "status": 200,
                        "data": {
                            "user_message": message,
                            "ai_response": confirm_msg,
                            "schedule_info": {"selected_date": selected_date, "selected_time": selected_start_time, "end_time": selected_end_time},
                            "calendar_event": None,
                            "usage": None,
                            "a2a_started": True
                        }
                    }
                
                # [기존 로직] 시작 시간 파싱
                selected_time = None
                time_condition = date_selected_context.get("time_condition")
                
                # "6시", "오후 2시", "18:00" 등 파싱
                time_match = re.search(r'(\d{1,2})\s*[시:]', message)
                if time_match:
                    hour = int(time_match.group(1))
                    
                    # 오후/오전 처리
                    if "오후" in message and hour < 12:
                        hour += 12
                    elif "오전" in message and hour == 12:
                        hour = 0
                    elif "오전" not in message and "오후" not in message and hour < 7:
                        # 7시 미만이고 오전/오후 명시 없으면 오후로 추정
                        hour += 12
                    
                    selected_time = f"{hour:02d}:00"
                    # logger.info(f"[Time Selection] 시간 파싱: {message} -> {selected_time}")
                
                if selected_time:
                    hour = int(selected_time.split(":")[0])
                    
                    # 시간 조건 검증
                    is_valid = True
                    rejection_msg = None
                    
                    if time_condition:
                        cond_match = re.search(r'(\d+)시\s*(이후|이전)', time_condition)
                        if cond_match:
                            cond_hour = int(cond_match.group(1))
                            cond_type = cond_match.group(2)
                            
                            if cond_type == "이후" and hour < cond_hour:
                                is_valid = False
                                rejection_msg = f"😅 해당 시간은 불가능해요. {time_condition}로 말씀해주세요!"
                            elif cond_type == "이전" and hour >= cond_hour:
                                is_valid = False
                                rejection_msg = f"😅 해당 시간은 불가능해요. {time_condition}로 말씀해주세요!"
                    
                    if not is_valid:
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=rejection_msg,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,
                            metadata=date_selected_context  # 컨텍스트 유지
                        )
                        return {
                            "status": 200,
                            "data": {
                                "user_message": message,
                                "ai_response": rejection_msg,
                                "schedule_info": {"invalid_time": True},
                                "calendar_event": None,
                                "usage": None
                            }
                        }
                    
                    # [NEW] 시작 시간 확보됨 → 끝나는 시간 물어보기 (A2A 바로 시작 안 함!)
                    selected_date = date_selected_context.get("selected_date")
                    friend_ids = date_selected_context.get("friend_ids", [])
                    activity = date_selected_context.get("activity")
                    location = date_selected_context.get("location")
                    friend_names = date_selected_context.get("friend_names", [])
                    
                    end_time_question = f"⏰ 시작 시간은 {selected_time}이군요! 끝나는 시간은 언제일까요?\n(예: 8시, 오후 10시) 모르시면 '몰라'라고 해주세요!"
                    
                    await ChatRepository.create_chat_log(
                        user_id=user_id,
                        request_text=None,
                        response_text=end_time_question,
                        friend_id=None,
                        message_type="ai_response",
                        session_id=session_id,
                        metadata={
                            "date_selected_mode": True,
                            "waiting_for_end_time": True,  # NEW FLAG
                            "selected_date": selected_date,
                            "selected_start_time": selected_time,
                            "time_condition": time_condition,
                            "friend_ids": friend_ids,
                            "friend_names": friend_names,
                            "activity": activity,
                            "location": location
                        }
                    )
                    
                    return {
                        "status": 200,
                        "data": {
                            "user_message": message,
                            "ai_response": end_time_question,
                            "schedule_info": {"selected_date": selected_date, "selected_start_time": selected_time, "waiting_for_end_time": True},
                            "calendar_event": None,
                            "usage": None,
                            "waiting_for_end_time": True
                        }
                    }
            
            # [✅ NEW] 개인 일정 끝 시간 응답 처리 (personal_schedule_mode)
            personal_schedule_context = None
            for log in recent_logs:
                meta = log.get("metadata") or {}
                if meta.get("personal_schedule_mode") and meta.get("waiting_for_end_time"):
                    personal_schedule_context = meta
                    break
            
            if personal_schedule_context:
                saved_schedule_info = personal_schedule_context.get("schedule_info", {})
                parsed_start_time = personal_schedule_context.get("parsed_start_time", "")
                original_message = personal_schedule_context.get("original_message", "")
                
                # "몰라", "모르겠어" 등 모르겠다는 응답 감지
                dont_know_patterns = ["몰라", "모르겠", "없어", "아닝", "글쎄", "미정", "아직", "잘 모", "모름", "아무", "상관없"]
                is_dont_know = any(p in message for p in dont_know_patterns)
                
                if is_dont_know:
                    # 기본값: 1시간 후
                    start_hour = int(parsed_start_time.split(":")[0]) if ":" in parsed_start_time else 14
                    end_hour = (start_hour + 1) % 24
                    selected_end_time = f"{end_hour:02d}:00"
                    end_time_msg = f"알겠습니다! 끝나는 시간은 1시간 후({selected_end_time})로 설정할게요 ⏰"
                    # logger.info(f"[개인 일정] 모르겠다 응답 -> 1시간 기본값 적용")
                else:
                    # 끝나는 시간 파싱 시도
                    time_match = re.search(r'(\d{1,2})\s*[시:]', message)
                    if time_match:
                        hour = int(time_match.group(1))
                        
                        # 오후/오전 처리
                        if "오후" in message and hour < 12:
                            hour += 12
                        elif "오전" in message and hour == 12:
                            hour = 0
                        elif "오전" not in message and "오후" not in message and hour < 7:
                            hour += 12
                        
                        selected_end_time = f"{hour:02d}:00"
                        end_time_msg = None
                        # logger.info(f"[개인 일정] 끝 시간 파싱: {message} -> {selected_end_time}")
                    else:
                        # 파싱 실패 시 다시 물어보기
                        retry_msg = "🤔 끝나는 시간을 이해하지 못했어요. 다시 한 번 알려주시겠어요? (예: 3시, 오후 5시) 또는 모르시면 '몰라'라고 해주세요!"
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=retry_msg,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,
                            metadata=personal_schedule_context  # 컨텍스트 유지
                        )
                        return {
                            "status": 200,
                            "data": {
                                "user_message": message,
                                "ai_response": retry_msg,
                                "schedule_info": {"waiting_for_end_time": True},
                                "calendar_event": None,
                                "usage": None
                            }
                        }
                
                # 끝 시간 확보됨 → 캘린더에 등록
                # schedule_info에 end_time 추가
                saved_schedule_info["end_time"] = selected_end_time
                saved_schedule_info["start_time"] = parsed_start_time
                
                # 끝 시간 메시지가 있으면 먼저 전송
                if end_time_msg:
                    await ChatRepository.create_chat_log(
                        user_id=user_id,
                        request_text=None,
                        response_text=end_time_msg,
                        friend_id=None,
                        message_type="ai_response",
                        session_id=session_id,
                    )
                
                # 캘린더에 일정 추가
                calendar_event = await ChatService._add_schedule_to_calendar(user_id, saved_schedule_info, original_text=original_message)
                
                if calendar_event:
                    if calendar_event.get("conflict"):
                        confirm_msg = f"⚠️ {calendar_event.get('message')}"
                    else:
                        confirm_msg = f"✅ 일정이 추가되었습니다: {calendar_event.get('summary')} ({parsed_start_time}~{selected_end_time})"
                else:
                    confirm_msg = "😅 일정 등록 중 문제가 발생했습니다. 다시 시도해주세요."
                
                await ChatRepository.create_chat_log(
                    user_id=user_id,
                    request_text=None,
                    response_text=confirm_msg,
                    friend_id=None,
                    message_type="ai_response",
                    session_id=session_id,
                )
                
                return {
                    "status": 200,
                    "data": {
                        "user_message": message,
                        "ai_response": confirm_msg,
                        "schedule_info": saved_schedule_info,
                        "calendar_event": calendar_event,
                        "usage": None
                    }
                }
            
            # 3. 추천 응답 확인 및 날짜 선택 파싱
            recommendation_context = None
            for log in recent_logs:
                meta = log.get("metadata") or {}
                if meta.get("recommendation_mode") and meta.get("recommendations"):
                    recommendation_context = meta
                    break
            
            if recommendation_context:
                # 추천 선택 파싱 시도
                selected_date = None
                recommendations = recommendation_context.get("recommendations", [])
                
                # "1번", "1", "1️⃣" 형식 파싱
                number_match = re.search(r'(\d+)\s*번?', message)
                if number_match:
                    idx = int(number_match.group(1)) - 1
                    if 0 <= idx < len(recommendations):
                        selected_date = recommendations[idx]["date"]
                        # logger.info(f"[Selection] 번호 선택: {idx+1}번 -> {selected_date}")
                
                # "12/25", "12월 25일" 형식 파싱
                if not selected_date:
                    date_match = re.search(r'(\d{1,2})[/월]?\s*(\d{1,2})', message)
                    if date_match:
                        month = int(date_match.group(1))
                        day = int(date_match.group(2))
                        # 현재 연도 또는 내년으로 맞추기
                        year = datetime.now().year
                        if month < datetime.now().month:
                            year += 1
                        target_date = f"{year}-{month:02d}-{day:02d}"
                        
                        # 추천 목록에서 찾기
                        for rec in recommendations:
                            if rec["date"] == target_date:
                                selected_date = target_date
                                # logger.info(f"[Selection] 날짜 선택: {target_date}")
                                break
                
                # "22일" (일만 있는 경우) 파싱
                if not selected_date:
                    day_only_match = re.search(r'(\d{1,2})일', message)
                    if day_only_match:
                        day = int(day_only_match.group(1))
                        # 추천 목록에서 해당 일자 찾기
                        for rec in recommendations:
                            rec_day = int(rec["date"].split("-")[2])
                            if rec_day == day:
                                selected_date = rec["date"]
                                # logger.info(f"[Selection] 일자 선택: {day}일 -> {selected_date}")
                                break
                
                if selected_date:
                    # 날짜 선택됨 → 시간 물어보기 (바로 A2A 시작하지 않음)
                    friend_ids = recommendation_context.get("friend_ids", [])
                    friend_names = recommendation_context.get("friend_names", [])
                    activity = recommendation_context.get("activity")
                    location = recommendation_context.get("location")
                    
                    # 시간 조건 찾기
                    selected_rec = next((r for r in recommendations if r["date"] == selected_date), None)
                    time_condition = selected_rec.get("condition") if selected_rec else None
                    
                    # 조건에 따른 시간 안내 메시지
                    if time_condition and "이후" in time_condition:
                        time_hint = f" ({time_condition}로 가능해요)"
                    elif time_condition and "이전" in time_condition:
                        time_hint = f" ({time_condition}로 가능해요)"
                    else:
                        time_hint = ""
                    
                    # 날짜 포맷팅
                    from datetime import datetime as dt_cls
                    try:
                        dt_obj = dt_cls.strptime(selected_date, "%Y-%m-%d")
                        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
                        date_display = f"{dt_obj.month}/{dt_obj.day}({weekdays[dt_obj.weekday()]})"
                    except:
                        date_display = selected_date
                    
                    time_question = f"📅 {date_display}로 선택하셨습니다!{time_hint}\n원하시는 시간이 있을까요? (예: 6시, 오후 2시)"
                    
                    await ChatRepository.create_chat_log(
                        user_id=user_id,
                        request_text=None,
                        response_text=time_question,
                        friend_id=None,
                        message_type="ai_response",
                        session_id=session_id,
                        metadata={
                            "date_selected_mode": True,
                            "selected_date": selected_date,
                            "time_condition": time_condition,
                            "friend_ids": friend_ids,
                            "friend_names": friend_names,
                            "activity": activity,
                            "location": location
                        }
                    )
                    
                    return {
                        "status": 200,
                        "data": {
                            "user_message": message,
                            "ai_response": time_question,
                            "schedule_info": {"selected_date": selected_date},
                            "calendar_event": None,
                            "usage": None,
                            "date_selected_mode": True
                        }
                    }
            
            # 3. [✅ 병렬화] 의도 파악 + 응답 생성을 동시에 실행
            # 일정 추출과 LLM 응답 생성을 병렬로 실행하여 속도 향상
            import asyncio
            
            # OpenAI 서비스 인스턴스 생성 (병렬 작업 전에 미리 생성)
            openai_service = OpenAIService()
            
            # 대화 히스토리 미리 가져오기 (병렬 작업에 필요)
            conversation_history = await ChatService._get_conversation_history(
                user_id,
                session_id=session_id,
            )
            
            # 병렬 실행 (클로저 대신 직접 호출)
            schedule_info, fallback_ai_result = await asyncio.gather(
                IntentService.extract_schedule_info(message),
                openai_service.generate_response(message, conversation_history)
            )
            
            # logger.info(f"[병렬화] 일정 추출 + 응답 생성 완료")
            
            friend_names_list = schedule_info.get("friend_names")
            friend_name = schedule_info.get("friend_name") if schedule_info.get("has_schedule_request") else None

            # [✅ NEW] LLM 환각 방지: 원본 메시지에 친구 이름이 실제로 있는지 검증
            # LLM이 "민서", "호이" 등을 환각해도, 메시지에 없으면 무시
            def validate_friend_names_in_message(names: list, msg: str) -> list:
                if not names:
                    return []
                validated = []
                for name in names:
                    if name and name in msg:
                        validated.append(name)
                    else:
                        logger.warning(f"[환각 방지] LLM이 '{name}'을 친구로 인식했으나, 원본 메시지에 없음 → 무시")
                return validated
            
            if friend_names_list:
                friend_names_list = validate_friend_names_in_message(friend_names_list, message)
                schedule_info["friend_names"] = friend_names_list  # 검증된 값으로 업데이트
            
            if friend_name and friend_name not in message:
                logger.warning(f"[환각 방지] LLM이 '{friend_name}'을 친구로 인식했으나, 원본 메시지에 없음 → 무시")
                friend_name = None
                schedule_info["friend_name"] = None

            if friend_names_list and len(friend_names_list) > 1:
                friend_names = friend_names_list
            elif friend_name:
                friend_names = [friend_name]
            else:
                friend_names = []

            # logger.info(f"[CHAT] schedule_info: {schedule_info}")

            # [✅ NEW] Slot Filling Logic
            # 일정 의도가 확실하지만 필수 정보가 누락된 경우 즉시 되묻기
            # 단, UI에서 친구를 이미 선택한 경우 friend_name/friend_names는 missing에서 제외
            missing = list(schedule_info.get("missing_fields") or [])
            
            # UI에서 친구 선택했으면 missing_fields에서 제거
            if selected_friend_ids:
                missing = [f for f in missing if f not in ["friend_name", "friend_names"]]
            
            # activity, title, location은 없어도 일정 조율 진행 가능하므로 제거
            missing = [f for f in missing if f not in ["activity", "title", "location"]]
            
            # 진짜 중요한 정보(날짜, 시간)만 누락된 경우에만 되묻기
            if schedule_info.get("has_schedule_request") and missing and not selected_friend_ids:
                # 친구 선택 없이 일정 요청 + 중요 정보 누락 -> 되묻기
                # logger.info(f"[Slot Filling] 누락된 정보 감지: {missing}")
                
                openai_service = OpenAIService()
                question = await openai_service.generate_slot_filling_question(missing, schedule_info)
                
                # 질문 저장 및 반환
                await ChatRepository.create_chat_log(
                    user_id=user_id,
                    request_text=None,
                    response_text=question,
                    friend_id=None,
                    message_type="ai_response",
                    session_id=session_id,
                )
                
                return {
                    "status": 200,
                    "data": {
                        "user_message": message,
                        "ai_response": question,
                        "schedule_info": schedule_info,
                        "calendar_event": None,
                        "usage": None
                    }
                }

            # [✅ 수정 1] 변수 초기화 (500 에러 방지)
            ai_result: Dict[str, Any] = {}
            ai_response: Optional[str] = None
            openai_service = OpenAIService()

            recoordination_needed = False
            thread_id_for_recoordination: Optional[str] = None
            session_ids_for_recoordination: List[str] = []

            # --- 재조율 감지 로직 ---
            # from config.database import supabase # [FIX] 상단 global import 사용
            from datetime import timezone

            # 이 시간보다 이전에 일어난 '거절'은 이미 해결된(지나간) 일이므로 무시하기 위함입니다.
            last_success_time = datetime.min.replace(tzinfo=timezone.utc)

            # 최근 10개 로그 중 'all_approved: True'인 가장 최신 로그 찾기
            success_check = supabase.table('chat_log').select('*').eq('user_id', user_id).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(10).execute()
            if success_check.data:
                for log in success_check.data:
                    meta = log.get('metadata', {})
                    if meta.get('all_approved') is True:
                        # 문자열 시간을 datetime으로 변환
                        try:
                            # created_at 형식에 따라 처리 (Z 또는 +00:00)
                            t_str = log['created_at'].replace('Z', '+00:00')
                            log_time = datetime.fromisoformat(t_str)
                            if log_time > last_success_time:
                                last_success_time = log_time
                                # 가장 최신 성공 하나만 찾으면 됨 (정렬되어 있으므로)
                                break
                        except Exception:
                            pass

            # 1) 최근 '거절(아니오)' 응답 확인 (schedule_approval_response)
            # limit(1) -> limit(5)로 늘려서 최근 대화 중 거절이 있었는지 확인
            rejection_response = supabase.table('chat_log').select('*').eq('user_id', user_id).eq('message_type', 'approval_response').order('created_at', desc=True).limit(5).execute()

            if rejection_response.data:
                # 최근 5개 로그 중 '거절(approved: false)'이 있고, 그 이후에 '승인(approved: true)'가 없으면 재조율 대상으로 판단
                for log in rejection_response.data:
                    meta = log.get('metadata', {})

                    # [✅ 추가 2] 거절 시점이 마지막 성공 시점보다 과거라면 무시 (이미 해결된 건)
                    try:
                        log_time = datetime.fromisoformat(log['created_at'].replace('Z', '+00:00'))
                        if log_time < last_success_time:
                            continue  # 건너뜀
                    except Exception:
                        pass

                    if not meta.get('approved', True) and meta.get('thread_id'):
                        # 거절 이력 발견
                        # 여기서 바로 True로 하지 않고, 이 거절 이후에 성공한 세션이 없는지 체크하면 더 좋지만 일단 간단하게 처리
                        if schedule_info.get("date") or schedule_info.get("time") or message.strip():
                            recoordination_needed = True
                            thread_id_for_recoordination = meta.get('thread_id')
                            session_ids_for_recoordination = meta.get('session_ids', [])
                            # logger.info(f"재조율 감지 (사용자 거절): thread_id={thread_id_for_recoordination}")
                            break

            # 2) 시스템으로부터 '거절 알림'을 받은 경우 확인 (schedule_rejection 또는 ai_response 내의 needs_recoordination)
            if not recoordination_needed:
                # message_type이 schedule_rejection 이거나, metadata에 needs_recoordination이 있는 ai_response 조회
                # OR 조건이 복잡하므로 두 번 쿼리하거나, 가장 최근 로그를 확인

                # A. schedule_rejection 확인
                sys_reject = supabase.table('chat_log').select('*').eq('user_id', user_id).eq('message_type', 'schedule_rejection').order('created_at', desc=True).limit(3).execute()
                if sys_reject.data:
                    for log in sys_reject.data:
                        # [✅ 추가 2] 시간 체크
                        try:
                            log_time = datetime.fromisoformat(log['created_at'].replace('Z', '+00:00'))
                            if log_time < last_success_time:
                                continue
                        except Exception:
                            pass

                        meta = log.get('metadata', {})
                        if meta.get('needs_recoordination') and meta.get('thread_id'):
                            if schedule_info.get("date") or schedule_info.get("time") or message.strip():
                                recoordination_needed = True
                                thread_id_for_recoordination = meta.get('thread_id')
                                session_ids_for_recoordination = meta.get('session_ids', [])
                                # logger.info(f"재조율 감지 (시스템 거절 알림): thread_id={thread_id_for_recoordination}")
                                break

                # B. AI가 보낸 "재조율을 위해..." 메시지 확인 (ai_response)
                if not recoordination_needed:
                    ai_reject = supabase.table('chat_log').select('*').eq('user_id', user_id).eq('message_type', 'ai_response').order('created_at', desc=True).limit(3).execute()
                    if ai_reject.data:
                        for log in ai_reject.data:
                            # [✅ 추가 2] 시간 체크
                            try:
                                log_time = datetime.fromisoformat(log['created_at'].replace('Z', '+00:00'))
                                if log_time < last_success_time:
                                    continue
                            except Exception:
                                pass

                            meta = log.get('metadata', {})
                            # [핵심] 로그 상 'metadata': {'needs_recoordination': true, ...} 가 있는지 확인
                            if meta and meta.get('needs_recoordination') and meta.get('thread_id'):
                                if schedule_info.get("date") or schedule_info.get("time") or message.strip():
                                    recoordination_needed = True
                                    thread_id_for_recoordination = meta.get('thread_id')
                                    session_ids_for_recoordination = meta.get('session_ids', [])
                                    # logger.info(f"재조율 감지 (AI 재조율 요청): thread_id={thread_id_for_recoordination}")
                                    break

            # [FIX] 명시적인 친구 이름이 감지되면 재조율 로직(과거 참여자 복구)을 무시하고 새로운 요청으로 처리
            # 이렇게 해야 "민서랑 성신조이랑"이라고 했을 때 과거의 "성신조이"만 있는 세션으로 돌아가지 않음
            if schedule_info.get("friend_names") or schedule_info.get("friend_name"):
                if recoordination_needed:
                    # logger.info(f"명시적인 친구 이름({schedule_info.get('friend_names') or schedule_info.get('friend_name')})이 감지되어 재조율 모드를 해제합니다.")
                    recoordination_needed = False
                    thread_id_for_recoordination = None
                    session_ids_for_recoordination = []

            # [✅ 수정] 명시적으로 선택된 친구가 있으면 우선 처리 및 일정 관련으로 강제 설정
            friend_ids: List[str] = []
            friend_id_to_name: Dict[str, str] = {}
            
            if selected_friend_ids:
                # logger.info(f"사용자가 선택한 친구 ID 사용: {selected_friend_ids}")
                friend_ids = selected_friend_ids
                # 이름 조회
                user_names = await ChatRepository.get_user_names_by_ids(friend_ids)
                friend_id_to_name = {fid: user_names.get(fid, '사용자') for fid in friend_ids}
                friend_names = [friend_id_to_name.get(fid, '사용자') for fid in friend_ids]
                
                # 선택된 친구가 있으면 재조율 로직 무시 (새로운 요청으로 간주)
                recoordination_needed = False
                thread_id_for_recoordination = None
                session_ids_for_recoordination = []
                
                # [중요] 친구를 선택했다면 무조건 일정 조율 모드로 진입
                is_schedule_related = True
            else:
                # [판단] 일정 요청이거나 재조율이면 -> AI 생성 스킵
                is_schedule_related = schedule_info.get("has_schedule_request") or recoordination_needed

            # 스케줄 관련 로직 실행
            if is_schedule_related:
                ai_response = None

                # 3. 친구 ID 찾기 (위에서 처리되지 않은 경우)
                if not friend_ids:
                    if recoordination_needed:
                        # [✅ 수정 2] 재조율 시 친구 정보 복구 확실하게 처리
                        from src.a2a.a2a_repository import A2ARepository
                        # session_ids가 있으면 그것으로, 없으면 thread_id로 찾기
                        target_sessions: List[Dict[str, Any]] = []
                        # 1. session_ids로 조회 시도
                        if session_ids_for_recoordination:
                            for sid in session_ids_for_recoordination:
                                sess = await A2ARepository.get_session(sid)
                                if sess:
                                    target_sessions.append(sess)

                        # 2. 실패 시 thread_id로 조회 시도
                        if not target_sessions and thread_id_for_recoordination:
                            target_sessions = await A2ARepository.get_thread_sessions(thread_id_for_recoordination)

                        if target_sessions:
                            # 모든 참여자 ID 수집 (나 제외)
                            all_pids = set()
                            for s in target_sessions:
                                # place_pref의 participants가 가장 정확함
                                place_pref = s.get('place_pref') or {}
                                if isinstance(place_pref, dict) and place_pref.get('participants'):
                                    for p in place_pref['participants']:
                                        all_pids.add(p)

                                # initiator/target 확인
                                if s.get('initiator_user_id'):
                                    all_pids.add(s['initiator_user_id'])
                                if s.get('target_user_id'):
                                    all_pids.add(s['target_user_id'])

                            # 나(user_id) 제외
                            if user_id in all_pids:
                                all_pids.remove(user_id)

                            friend_ids = list(all_pids)

                            if friend_ids:
                                # 이름 조회
                                user_names = await ChatRepository.get_user_names_by_ids(friend_ids)
                                friend_id_to_name = {fid: user_names.get(fid, '사용자') for fid in friend_ids}
                                friend_names = [friend_id_to_name.get(fid, '사용자') for fid in friend_ids]
                                # logger.info(f"재조율 참여자 복구 성공: {friend_names} (IDs: {friend_ids})")
                            else:
                                logger.error("재조율 참여자 복구 실패: 친구 ID를 찾을 수 없음")
                        else:
                            logger.error("재조율 세션 정보를 찾을 수 없습니다.")

                    else:
                        # 신규 요청 (기존 유지)
                        for name in friend_names:
                            fid = await ChatService._find_friend_id_by_name(user_id, name)
                            if fid:
                                friend_ids.append(fid)
                                friend_id_to_name[fid] = name

            # -------------------------------------------------------
            # A2A 세션 시작
            # -------------------------------------------------------

            response_sent_to_db = False

            # [✅ 중요] friend_ids가 비어있으면 A2A 로직을 타지 않음 -> 단독 일정으로 빠지는 것을 방지해야 함
            # 재조율인데 친구를 못 찾았으면 에러 처리
            if recoordination_needed and not friend_ids:
                ai_response = "이전 대화의 참여자 정보를 찾을 수 없어 재조율을 진행할 수 없습니다. 다시 시도해 주세요."
                # 여기서 리턴해서 아래 캘린더 추가 로직으로 빠지는 것 방지
            elif is_schedule_related and friend_ids:
                try:
                    from src.a2a.a2a_service import A2AService
                    from src.a2a.a2a_repository import A2ARepository
                    from src.auth.auth_repository import AuthRepository

                    # "조율 중" 메시지
                    if len(friend_names) > 1:
                        wait_msg = f"🤖 {', '.join(friend_names)}님들의 Agent와 일정을 조율하고 있습니다..."
                    else:
                        wait_msg = f"🤖 {friend_names[0]}님의 Agent와 일정을 조율하고 있습니다..."

                    first_friend_id = friend_ids[0] if friend_ids else None
                    await ChatRepository.create_chat_log(
                        user_id=user_id,
                        request_text=None,
                        response_text=wait_msg,
                        friend_id=first_friend_id if len(friend_ids) == 1 else None,
                        message_type="ai_response",
                        session_id=session_id,
                    )
                    response_sent_to_db = True
                    ai_response = wait_msg

                    # [✅ UPDATED] 스마트 추천 vs 바로 협상 결정
                    # 날짜와 시간이 명확하면 추천 건너뛰고 바로 A2A 협상
                    has_explicit_date = bool(schedule_info.get("start_date"))
                    # 시간이 "명시적으로" 언급되었는지 확인 (LLM이 기본값이나 잘못된 값을 넣을 수 있음)
                    time_text = schedule_info.get("time") or ""
                    # 실제 시간 표현인지 검증 (시간 키워드가 있어야 함)
                    time_keywords = ["시", "분", "오전", "오후", "아침", "점심", "저녁", "밤", "새벽", ":"]
                    is_real_time = any(kw in time_text for kw in time_keywords) if time_text else False
                    # [✅ FIX] time 필드도 확인 (start_time이 없어도 time이 있으면 명시적 시간으로 판단)
                    has_explicit_time = (bool(schedule_info.get("start_time")) or is_real_time)
                    is_date_range = schedule_info.get("start_date") != schedule_info.get("end_date") if schedule_info.get("end_date") else False
                    
                    # 디버그 로깅
                    # logger.info(f"[DEBUG] has_explicit_date={has_explicit_date}, has_explicit_time={has_explicit_time}")
                    # logger.info(f"[DEBUG] time_text='{time_text}', start_time='{schedule_info.get('start_time')}', is_real_time={is_real_time}, is_date_range={is_date_range}")
                    
                    # 날짜+시간 둘 다 명확하고, 범위가 아닌 특정 날짜면 바로 협상
                    # ✅ [여행 모드] duration_nights > 0이면 날짜 범위가 있어도 바로 A2A 시작
                    is_travel_mode = duration_nights > 0
                    should_skip_recommendation = (has_explicit_date and has_explicit_time and not is_date_range) or (is_travel_mode and has_explicit_date)
                    should_use_recommendation = len(friend_ids) >= 1 and not recoordination_needed and not should_skip_recommendation
                    
                    # [✅ NEW] 날짜는 있지만 시간이 없으면 → 시간 물어보기
                    if has_explicit_date and not has_explicit_time and not is_date_range and len(friend_ids) >= 1:
                        selected_date = schedule_info.get("start_date")
                        activity = schedule_info.get("activity")
                        location = schedule_info.get("location")
                        
                        # 날짜 포맷팅
                        from datetime import datetime as dt_cls
                        try:
                            dt_obj = dt_cls.strptime(selected_date, "%Y-%m-%d")
                            weekdays = ["월", "화", "수", "목", "금", "토", "일"]
                            date_display = f"{dt_obj.month}/{dt_obj.day}({weekdays[dt_obj.weekday()]})"
                        except:
                            date_display = selected_date
                        
                        time_question = f"📅 {date_display}로 일정을 잡으려고 해요!\n원하시는 시간이 있을까요? (예: 6시, 오후 2시)"
                        
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=time_question,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,
                            metadata={
                                "date_selected_mode": True,
                                "selected_date": selected_date,
                                "time_condition": None,
                                "friend_ids": friend_ids,
                                "friend_names": friend_names,
                                "activity": activity,
                                "location": location
                            }
                        )
                        
                        return {
                            "status": 200,
                            "data": {
                                "user_message": message,
                                "ai_response": time_question,
                                "schedule_info": {"selected_date": selected_date},
                                "calendar_event": None,
                                "usage": None,
                                "date_selected_mode": True
                            }
                        }
                    
                    if should_skip_recommendation and len(friend_ids) >= 1:
                        # 바로 A2A 협상 시작
                        from src.a2a.a2a_service import A2AService
                        
                        selected_date = schedule_info.get("start_date")
                        selected_time = schedule_info.get("start_time") or "09:00"  # ✅ 기본 시간
                        activity = schedule_info.get("activity")
                        location = schedule_info.get("location") or explicit_location
                        
                        # [✅ NEW] 끝 시간이 명시되지 않았으면 물어보기 (여행 모드는 제외)
                        end_time_from_info = schedule_info.get("end_time")
                        end_time_keywords = ["까지", "끝", "종료", "~", "부터", "시간 동안", "동안"]
                        user_mentioned_end_time = any(kw in message for kw in end_time_keywords)
                        
                        # 사용자가 끝 시간을 언급하지 않았으면 LLM의 end_time 무시
                        if end_time_from_info and not user_mentioned_end_time:
                            logger.warning(f"[A2A 환각 방지] LLM이 end_time='{end_time_from_info}'을 반환했지만, 사용자 메시지에 끝 시간 키워드 없음 → 무시")
                            end_time_from_info = None
                        
                        # ✅ [여행 모드] 여행일 경우 끝 시간 물어보기 스킵
                        if not end_time_from_info and not is_travel_mode:
                            # 끝 시간 물어보기
                            end_time_question = f"⏰ {selected_date} {selected_time}에 시작하는 약속이군요!\n끝나는 시간은 언제인가요? (예: 5시, 오후 7시)\n모르시면 '몫라'라고 해주세요!"
                            
                            await ChatRepository.create_chat_log(
                                user_id=user_id,
                                request_text=None,
                                response_text=end_time_question,
                                friend_id=None,
                                message_type="ai_response",
                                session_id=session_id,
                                metadata={
                                    "date_selected_mode": True,
                                    "waiting_for_end_time": True,
                                    "selected_date": selected_date,
                                    "selected_start_time": selected_time,
                                    "friend_ids": friend_ids,
                                    "activity": activity,
                                    "location": location
                                }
                            )
                            
                            return {
                                "status": 200,
                                "data": {
                                    "user_message": message,
                                    "ai_response": end_time_question,
                                    "schedule_info": {"selected_date": selected_date, "selected_start_time": selected_time, "waiting_for_end_time": True},
                                    "calendar_event": None,
                                    "usage": None,
                                    "waiting_for_end_time": True
                                }
                            }
                        
                        # 끝 시간이 있거나 여행 모드면 바로 A2A 시작
                        # duration_minutes 계산
                        if end_time_from_info:
                            start_hour = int(selected_time.split(":")[0]) if selected_time and ":" in selected_time else 14
                            end_hour = int(end_time_from_info.split(":")[0]) if end_time_from_info and ":" in end_time_from_info else (start_hour + 1)
                            if end_hour == 24:
                                end_hour = 0
                            duration_minutes = (end_hour - start_hour) * 60 if end_hour > start_hour else ((24 - start_hour) + end_hour) * 60
                            if duration_minutes <= 0:
                                duration_minutes = 60
                        else:
                            duration_minutes = 60
                        
                        # ✅ [여행 모드] 확인 메시지 변경
                        if is_travel_mode:
                            end_date = schedule_info.get("end_date") or selected_date
                            confirm_msg = f"✅ {selected_date}부터 {duration_nights}박 {duration_nights + 1}일 여행 일정을 상대방에게 요청을 보냈습니다. A2A 화면에서 확인해주세요!"
                        elif end_time_from_info:
                            confirm_msg = f"✅ {selected_date} {selected_time}~{end_time_from_info}로 상대방에게 요청을 보냈습니다. A2A 화면에서 확인해주세요!"
                        else:
                            confirm_msg = f"✅ {selected_date} {selected_time}로 상대방에게 요청을 보냈습니다. A2A 화면에서 확인해주세요!"
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=confirm_msg,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,
                        )
                        
                        # A2A 협상 시작
                        # [✅ 수정] explicit_title이 있으면 그것을 summary로 사용
                        final_summary = explicit_title or activity or "약속"
                        
                        a2a_result = await A2AService.start_multi_user_session(
                            initiator_user_id=user_id,
                            target_user_ids=friend_ids,
                            summary=final_summary,
                            date=selected_date,
                            time=selected_time,
                            end_time=end_time_from_info,  # [✅ NEW] 끝나는 시간 전달
                            location=location,
                            activity=final_summary,  # [✅ 수정] 캘린더 제목 동기화를 위해 activity에도 final_summary 전달
                            duration_minutes=duration_minutes,
                            force_new=True,
                            origin_chat_session_id=session_id,
                            duration_nights=duration_nights  # ✅ 박 수 전달
                        )
                        
                        return {
                            "status": 200,
                            "data": {
                                "user_message": message,
                                "ai_response": confirm_msg,
                                "schedule_info": {"selected_date": selected_date, "selected_time": selected_time, "end_time": end_time_from_info},
                                "calendar_event": None,
                                "usage": None,
                                "a2a_started": True
                            }
                        }
                    
                    if should_use_recommendation:
                        # 스마트 추천 모드
                        from src.a2a.negotiation_engine import NegotiationEngine
                        
                        KST = ZoneInfo("Asia/Seoul")
                        
                        # 날짜 범위 파싱 (없으면 오늘부터 2주)
                        try:
                            if schedule_info.get("start_date"):
                                start_dt = datetime.strptime(schedule_info.get("start_date"), "%Y-%m-%d").replace(tzinfo=KST)
                            else:
                                start_dt = datetime.now(KST)
                            
                            if schedule_info.get("end_date"):
                                end_dt = datetime.strptime(schedule_info.get("end_date"), "%Y-%m-%d").replace(tzinfo=KST)
                            else:
                                end_dt = start_dt + timedelta(days=14)
                        except:
                            start_dt = datetime.now(KST)
                            end_dt = start_dt + timedelta(days=14)
                        
                        # 시간 선호도
                        preferred_hour = None
                        if schedule_info.get("start_time"):
                            try:
                                preferred_hour = int(schedule_info.get("start_time").split(":")[0])
                            except:
                                pass
                        
                        # NegotiationEngine 생성 (실제 협상은 안 함, 분석만)
                        engine = NegotiationEngine(
                            session_id="temp_analysis",
                            initiator_user_id=user_id,
                            participant_user_ids=friend_ids,
                            activity=schedule_info.get("activity"),
                            location=schedule_info.get("location")
                        )
                        
                        # 모든 캘린더 수집
                        availabilities = await engine.collect_all_availabilities(start_dt, end_dt)
                        
                        # 교집합 분석
                        intersections = engine.find_intersection_slots(availabilities, preferred_hour)
                        
                        # 추천 결과 생성
                        recommendations = engine.recommend_best_dates(intersections, max_count=3)
                        
                        if recommendations:
                            # 추천 메시지 생성
                            rec_lines = ["📅 일정 조율 결과 추천 날짜입니다:\n"]
                            for i, rec in enumerate(recommendations):
                                rec_lines.append(f"{i+1}️⃣ {rec.display_text}")
                            rec_lines.append("\n번호나 날짜로 선택해주세요!")
                            
                            recommendation_msg = "\n".join(rec_lines)
                            
                            # 추천 결과 저장 (메타데이터에 저장해서 다음 메시지에서 파싱 가능)
                            await ChatRepository.create_chat_log(
                                user_id=user_id,
                                request_text=None,
                                response_text=recommendation_msg,
                                friend_id=None,
                                message_type="ai_response",
                                session_id=session_id,
                                metadata={
                                    "recommendation_mode": True,
                                    "recommendations": [
                                        {"date": r.date, "condition": r.condition} 
                                        for r in recommendations
                                    ],
                                    "friend_ids": friend_ids,
                                    "friend_names": friend_names,
                                    "activity": schedule_info.get("activity"),
                                    "location": schedule_info.get("location")
                                }
                            )
                            
                            return {
                                "status": 200,
                                "data": {
                                    "user_message": message,
                                    "ai_response": recommendation_msg,
                                    "schedule_info": schedule_info,
                                    "calendar_event": None,
                                    "usage": None,
                                    "recommendation_mode": True
                                }
                            }
                        else:
                            # 가능한 시간이 없음
                            no_slot_msg = "😅 안타깝게도 해당 기간에 모든 분이 가능한 시간이 없어요. 기간을 넓혀서 다시 찾아볼까요?"
                            await ChatRepository.create_chat_log(
                                user_id=user_id,
                                request_text=None,
                                response_text=no_slot_msg,
                                friend_id=None,
                                message_type="ai_response",
                                session_id=session_id,
                            )
                            return {
                                "status": 200,
                                "data": {
                                    "user_message": message,
                                    "ai_response": no_slot_msg,
                                    "schedule_info": schedule_info,
                                    "calendar_event": None,
                                    "usage": None
                                }
                            }

                    # 요약 메시지 (기존 로직) - explicit_title 우선 사용
                    if explicit_title:
                        summary = explicit_title
                    elif schedule_info.get("activity"):
                         summary = schedule_info.get("activity")
                    else:
                        summary_parts = []
                        if friend_names:
                             summary_parts.append(", ".join(friend_names))
                        if schedule_info.get("date"):
                             summary_parts.append(schedule_info.get("date"))
                        if schedule_info.get("time"):
                             summary_parts.append(schedule_info.get("time"))
                        summary = " ".join(summary_parts) if summary_parts else "약속"

                    if recoordination_needed:
                        # [재조율 로직]
                        user_info = await AuthRepository.find_user_by_id(user_id)
                        initiator_name = user_info.get("name", "사용자") if user_info else "사용자"

                        # 세션 상태 업데이트
                        for session_id_for_update in session_ids_for_recoordination:
                            await A2ARepository.update_session_status(session_id_for_update, "in_progress")

                        sessions_info = []
                        for session_id_for_update, friend_id in zip(session_ids_for_recoordination, friend_ids):
                            sessions_info.append({
                                "session_id": session_id_for_update,
                                "target_id": friend_id,
                                "target_name": friend_id_to_name.get(friend_id, "사용자")
                            })

                        a2a_result = await A2AService._execute_multi_user_coordination(
                            thread_id=thread_id_for_recoordination,
                            sessions=sessions_info,
                            initiator_user_id=user_id,
                            initiator_name=initiator_name,
                            date=schedule_info.get("date"),
                            time=schedule_info.get("time"),
                            location=schedule_info.get("location"),
                            activity=schedule_info.get("activity"),
                            duration_minutes=60,
                            reuse_existing=True
                        )
                        thread_id = thread_id_for_recoordination
                        session_ids = session_ids_for_recoordination
                    else:
                        # [신규 세션 로직]
                        # explicit 값이 있으면 우선 사용
                        final_activity = explicit_title or schedule_info.get("activity")
                        final_location = explicit_location or schedule_info.get("location")
                        final_start_time = explicit_start_time or schedule_info.get("time")
                        final_end_time = explicit_end_time or schedule_info.get("end_time")
                        
                        # duration_minutes 계산 (explicit 시간이 있는 경우)
                        calculated_duration = 60  # 기본값
                        if final_start_time and final_end_time and ":" in final_start_time and ":" in final_end_time:
                            try:
                                start_h, start_m = map(int, final_start_time.split(":"))
                                end_h, end_m = map(int, final_end_time.split(":"))
                                calculated_duration = (end_h * 60 + end_m) - (start_h * 60 + start_m)
                                if calculated_duration <= 0:
                                    calculated_duration = 60  # 음수인 경우 기본값
                            except:
                                pass
                        
                        a2a_result = await A2AService.start_multi_user_session(
                            initiator_user_id=user_id,
                            target_user_ids=friend_ids,
                            summary=summary,
                            date=schedule_info.get("date"),
                            time=final_start_time,
                            end_time=final_end_time,  # [✅ NEW] 끝나는 시간 전달
                            location=final_location,
                            activity=final_activity,
                            duration_minutes=calculated_duration,
                            force_new=True,  # [✅ 수정] 채팅에서 새로운 요청 시 무조건 새 세션 생성
                            origin_chat_session_id=session_id,  # [✅ 추가] 원본 채팅 세션 ID 전달
                            duration_nights=duration_nights  # ✅ 박 수 전달
                        )
                        thread_id = a2a_result.get("thread_id")
                        session_ids = a2a_result.get("session_ids", [])

                    # 결과 처리
                    needs_approval = a2a_result.get("needs_approval", False)
                    proposal = a2a_result.get("proposal")

                    if (recoordination_needed or a2a_result.get("status") == 200):
                        if needs_approval and proposal:
                            # [FIX] A2A 화면 안내 메시지가 이미 전송되므로 중복 메시지 제거
                            ai_response = None
                        elif a2a_result.get("needs_recoordination"):
                            # [FIX] a2a_service에서 이미 충돌 알림 메시지를 DB에 저장했으므로
                            # 여기서 또 ai_response로 반환하면 프론트엔드에서 중복으로 표시됨 (폴링 + 로컬 추가)
                            # 따라서 여기서는 ai_response를 비워서 중복 방지
                            ai_response = None

                except Exception as e:
                    logger.error(f"A2A 세션 시작 중 오류: {str(e)}")
                    ai_response = "일정 조율을 시도했지만 문제가 발생했습니다."
                    if response_sent_to_db:
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=ai_response,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,  # ✅ 세션 연결
                        )
                    else:
                        response_sent_to_db = False

            # 5. 캘린더 직접 추가 (A2A가 아닐 때만!!)
            # [✅ 수정 3] friend_ids가 있으면(=상대방이 있으면) 절대로 여기로 들어오면 안 됨
            calendar_event = None
            
            # [✅ NEW] 확정/부정 메시지 감지
            # 확정: "응", "네" 등 → 시작시간만 등록
            # 부정: "아닝", "아니" 등 → 끝나는 시간 없이 시작시간만 등록
            confirmation_keywords = ["응", "네", "네네", "그래", "등록해", "등록해줘", "맞아", "ㅇㅇ", "시작시간만", "시작 시간만"]
            negative_confirmation = ["아닝", "아니", "아니요", "아뇨", "몰라", "모름", "미정", "안정해졌어", "정해진거없어"]
            
            is_confirmation = (
                message.strip() in confirmation_keywords or 
                message.strip() in negative_confirmation or
                any(kw in message for kw in ["등록", "좋아", "그거로", "시작시간만"])
            )
            
            if not response_sent_to_db and not recoordination_needed and not friend_ids:
                # [NEW] 개인 일정인 경우, 날짜+시간이 있으면 먼저 중복 체크
                early_conflict_warning = None
                has_date = schedule_info.get("date") or schedule_info.get("start_date")
                has_time = schedule_info.get("time") or schedule_info.get("start_time")
                if has_date and has_time and not friend_ids:
                    try:
                        from src.auth.auth_service import AuthService
                        from src.calendar.calender_service import GoogleCalendarService
                        
                        # 날짜/시간 파싱 - start_date가 있으면 우선 사용 (더 정확함)
                        raw_date = schedule_info.get("start_date") or schedule_info.get("date", "")
                        
                        # start_date가 YYYY-MM-DD 형식이면 직접 파싱
                        if raw_date and "-" in raw_date:
                            try:
                                from zoneinfo import ZoneInfo
                                KST = ZoneInfo("Asia/Seoul")
                                check_date = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=KST)
                            except:
                                check_date = ChatService._parse_date(raw_date)
                        else:
                            check_date = ChatService._parse_date(raw_date)
                        
                        check_start, check_end = ChatService._parse_time(schedule_info.get("time"), check_date, context_text=message)
                        
                        # [FIX] start_time/end_time이 있으면 더 정확한 시간 사용
                        if schedule_info.get("start_time"):
                            try:
                                from zoneinfo import ZoneInfo
                                KST = ZoneInfo("Asia/Seoul")
                                time_parts = schedule_info["start_time"].split(":")
                                check_start = check_date.replace(hour=int(time_parts[0]), minute=int(time_parts[1]) if len(time_parts) > 1 else 0, second=0)
                                
                                if schedule_info.get("end_time"):
                                    end_parts = schedule_info["end_time"].split(":")
                                    check_end = check_date.replace(hour=int(end_parts[0]), minute=int(end_parts[1]) if len(end_parts) > 1 else 0, second=0)
                                else:
                                    check_end = check_start + timedelta(hours=1)  # 종료시간 없으면 1시간 후
                            except Exception as time_err:
                                logger.warning(f"start_time 파싱 실패: {time_err}")
                        
                        logger.info(f"[DATE_DEBUG] IntentService date='{schedule_info.get('date')}', start_date='{schedule_info.get('start_date')}' → parsed={check_date.strftime('%Y-%m-%d') if check_date else 'None'}")
                        logger.info(f"[TIME_DEBUG] start_time='{schedule_info.get('start_time')}', end_time='{schedule_info.get('end_time')}' → check_start={check_start}, check_end={check_end}")
                        
                        if check_start:
                            user_info = await AuthService.get_user_by_id(user_id)
                            if user_info and user_info.get("access_token"):
                                google_calendar = GoogleCalendarService()
                                day_start = check_start.replace(hour=0, minute=0, second=0)
                                day_end = check_start.replace(hour=23, minute=59, second=59)
                                
                                existing_events = await google_calendar.get_calendar_events(
                                    access_token=user_info["access_token"],
                                    time_min=day_start,
                                    time_max=day_end
                                )
                                
                                for evt in existing_events:
                                    evt_start_str = evt.start.get("dateTime") or evt.start.get("date")
                                    evt_end_str = evt.end.get("dateTime") or evt.end.get("date")
                                    
                                    if evt_start_str:
                                        if "T" not in evt_start_str:
                                            # 종일 이벤트
                                            user_name = user_info.get("name") or "회원"
                                            early_conflict_warning = f"⚠️ {user_name}님, 그 날에는 이미 '{evt.summary}' 일정이 있어요. 그래도 등록할까요?"
                                            break
                                        else:
                                            try:
                                                evt_start_dt = datetime.fromisoformat(evt_start_str.replace("Z", "+00:00"))
                                                evt_end_dt = datetime.fromisoformat(evt_end_str.replace("Z", "+00:00"))
                                                if check_start < evt_end_dt and check_end > evt_start_dt:
                                                    user_name = user_info.get("name") or "회원"
                                                    early_conflict_warning = f"⚠️ {user_name}님, 그 시간에는 이미 '{evt.summary}' 일정이 있어요. 다른 시간을 선택해 주세요!"
                                                    break
                                            except Exception as parse_err:
                                                logger.warning(f"이벤트 시간 파싱 실패: {parse_err}")
                    except Exception as e:
                        logger.warning(f"조기 중복 체크 오류: {e}")
                
                # 조기 중복 경고가 있으면 먼저 알려주기
                if early_conflict_warning:
                    ai_response = early_conflict_warning

                # Case 1: 현재 메시지에 날짜+시간 정보가 있는 경우
                elif schedule_info.get("has_schedule_request") and schedule_info.get("date") and schedule_info.get("time"):
                    # [수정] 단일 시간("3시에")인 경우 바로 등록하지 않고 AI가 종료 시간을 물어보게 함
                    # 범위 표현("부터", "까지", "~")이 있거나, "시작시간만" 같은 강제 키워드가 있을 때만 즉시 등록
                    time_str = schedule_info.get("time", "")
                    
                    # [✅ FIX] 끝 시간이 사용자 메시지에 실제로 언급되었는지 확인
                    # LLM이 end_time을 환각할 수 있으므로, 원본 메시지에서 끝 시간 키워드 확인
                    end_time_from_info = schedule_info.get("end_time")
                    end_time_keywords = ["까지", "끝", "종료", "~", "부터", "시간 동안", "동안"]
                    user_mentioned_end_time = any(kw in message for kw in end_time_keywords)
                    
                    # 사용자가 끝 시간을 언급하지 않았으면 LLM의 end_time 무시
                    if end_time_from_info and not user_mentioned_end_time:
                        logger.warning(f"[환각 방지] LLM이 end_time='{end_time_from_info}'을 반환했지만, 사용자 메시지에 끝 시간 키워드 없음 → 무시")
                        end_time_from_info = None
                    
                    if not end_time_from_info:
                        # 시작 시간 파싱
                        start_time_match = re.search(r'(\d{1,2})\s*[시:]', time_str)
                        if start_time_match:
                            hour = int(start_time_match.group(1))
                            if "오후" in time_str and hour < 12:
                                hour += 12
                            elif "오전" in time_str and hour == 12:
                                hour = 0
                            elif "오전" not in time_str and "오후" not in time_str and hour < 7:
                                hour += 12
                            parsed_start_time = f"{hour:02d}:00"
                        else:
                            parsed_start_time = time_str
                        
                        # logger.info(f"[개인 일정] 시작 시간 '{parsed_start_time}' 감지 - 끝 시간 물어보기")
                        
                        # 끝나는 시간 질문
                        end_time_question = f"⏰ {schedule_info.get('date')} {parsed_start_time}에 시작하는 일정이군요!\n끝나는 시간은 언제인가요? (예: 3시, 오후 5시)\n모르시면 '몰라'라고 해주세요!"
                        
                        await ChatRepository.create_chat_log(
                            user_id=user_id,
                            request_text=None,
                            response_text=end_time_question,
                            friend_id=None,
                            message_type="ai_response",
                            session_id=session_id,
                            metadata={
                                "personal_schedule_mode": True,
                                "waiting_for_end_time": True,
                                "schedule_info": schedule_info,
                                "parsed_start_time": parsed_start_time,
                                "original_message": message
                            }
                        )
                        
                        return {
                            "status": 200,
                            "data": {
                                "user_message": message,
                                "ai_response": end_time_question,
                                "schedule_info": schedule_info,
                                "calendar_event": None,
                                "usage": None,
                                "waiting_for_end_time": True
                            }
                        }
                    
                    # 끝 시간이 있으면 바로 등록
                    # logger.info(f"[개인 일정] 시간 '{time_str}' 감지 - 즉시 등록")
                    
                    calendar_event = await ChatService._add_schedule_to_calendar(user_id, schedule_info, original_text=message)
                    if calendar_event:
                        if calendar_event.get("conflict"):
                            ai_response = f"⚠️ {calendar_event.get('message')}"
                        else:
                            ai_response = f"✅ 일정이 추가되었습니다: {calendar_event.get('summary')}"
                
                # Case 2: 확정 메시지인 경우 - 이전 대화에서 일정 정보 추출
                elif is_confirmation:
                    # logger.info(f"[CHAT] 확정 메시지 감지: '{message}' - 이전 대화에서 일정 정보 추출 시도")
                    
                    # 이전 대화 기록에서 일정 정보 추출
                    recent_logs = await ChatRepository.get_recent_chat_logs(user_id, limit=10, session_id=session_id)
                    
                    collected_info = {
                        "date": None,
                        "time": None,
                        "title": None,
                        "activity": None,
                        "location": None
                    }
                    
                    # 최근 대화에서 정보 수집
                    for log in recent_logs:
                        text = log.get("request_text") or log.get("response_text") or ""
                        
                        # 각 메시지에서 일정 정보 추출 시도
                        temp_info = await IntentService.extract_schedule_info(text)
                        
                        # 누락된 정보만 채우기
                        if temp_info.get("date") and not collected_info["date"]:
                            collected_info["date"] = temp_info["date"]
                        if temp_info.get("time") and not collected_info["time"]:
                            collected_info["time"] = temp_info["time"]
                        if temp_info.get("title") and not collected_info["title"]:
                            collected_info["title"] = temp_info["title"]
                        if temp_info.get("activity") and not collected_info["activity"]:
                            collected_info["activity"] = temp_info["activity"]
                        if temp_info.get("location") and not collected_info["location"]:
                            collected_info["location"] = temp_info["location"]
                    
                    # logger.info(f"[CHAT] 수집된 일정 정보: {collected_info}")
                    
                    # 날짜와 시간이 있으면 등록
                    if collected_info.get("date") and collected_info.get("time"):
                        collected_info["has_schedule_request"] = True
                        calendar_event = await ChatService._add_schedule_to_calendar(
                            user_id, 
                            collected_info, 
                            original_text=collected_info.get("title") or collected_info.get("activity") or "일정"
                        )
                        if calendar_event:
                            if calendar_event.get("conflict"):
                                ai_response = f"⚠️ {calendar_event.get('message')}"
                            else:
                                ai_response = f"✅ 일정이 추가되었습니다: {calendar_event.get('summary')}"
                    elif collected_info.get("date"):
                        # 시간 없이 날짜만 있는 경우 - 시간 물어보기
                        ai_response = f"날짜는 {collected_info.get('date')}로 확인했어요. 몇 시에 시작하는 일정인가요?"

                # [NEW] Case 3: 날짜는 없지만 시간이 있는 경우 (예: "3시에") -> 이전 대화에서 날짜 가져오기
                elif schedule_info.get("time") and not schedule_info.get("date"):
                    # logger.info(f"[CHAT] 시간만 감지: '{message}' - 이전 대화에서 날짜 정보 추출 시도")
                    
                    recent_logs = await ChatRepository.get_recent_chat_logs(user_id, limit=10, session_id=session_id)
                    
                    # 현재 메시지 정보로 초기화
                    collected_info = {
                        "date": None,
                        "time": schedule_info.get("time"),
                        "title": schedule_info.get("title") or schedule_info.get("activity"),
                        "activity": schedule_info.get("activity"),
                        "location": schedule_info.get("location")
                    }
                    
                    # 최근 대화에서 부족한 정보(특히 날짜, 제목) 수집
                    for log in recent_logs:
                        text = log.get("request_text") or log.get("response_text") or ""
                        temp_info = await IntentService.extract_schedule_info(text)
                        
                        if temp_info.get("date") and not collected_info["date"]:
                            collected_info["date"] = temp_info["date"]
                        if temp_info.get("title") and not collected_info["title"]:
                            collected_info["title"] = temp_info["title"]
                        if temp_info.get("activity") and not collected_info["activity"]:
                            collected_info["activity"] = temp_info["activity"]
                            
                    # 날짜가 찾아지면 등록 시도
                    if collected_info.get("date"):
                        collected_info["has_schedule_request"] = True
                        calendar_event = await ChatService._add_schedule_to_calendar(
                            user_id, 
                            collected_info, 
                            original_text=message
                        )
                        if calendar_event:
                            if calendar_event.get("conflict"):
                                ai_response = f"⚠️ {calendar_event.get('message')}"
                            else:
                                ai_response = f"✅ 일정이 추가되었습니다: {calendar_event.get('summary')}"

                        # 6. 응답이 없는 경우 (스케줄 정보 부족 또는 일반 대화) -> 병렬로 미리 생성된 응답 사용
            if ai_response is None and not response_sent_to_db:
                # [✅ 병렬화] 이미 병렬로 생성된 응답 사용 (추가 LLM 호출 없음)
                if fallback_ai_result and fallback_ai_result.get("message"):
                    ai_response = fallback_ai_result["message"]
                    logger.info(f"[병렬화] 미리 생성된 응답 사용")


            # 7. 일반 대화 저장
            if not response_sent_to_db and ai_response:
                first_friend_id = friend_ids[0] if friend_ids else None
                await ChatRepository.create_chat_log(
                    user_id=user_id,
                    request_text=None,
                    response_text=ai_response,
                    friend_id=first_friend_id if len(friend_ids) == 1 else None,
                    message_type="ai_response",
                    session_id=session_id,  # ✅ 세션 연결
                )

            logger.info(f"AI 대화 완료 - 사용자: {user_id}")
            
            # WebSocket으로 실시간 알림 전송
            await ChatService.send_ws_notification(user_id, "new_message", {
                "message": ai_response,
                "session_id": session_id,
                "sender": "ai"
            })

            # [✅ 수정 1 관련] ai_result.get('usage') 접근 시 안전하게 처리
            return {
                "status": 200,
                "data": {
                    "user_message": message,
                    "ai_response": ai_response,
                    "schedule_info": schedule_info,
                    "calendar_event": calendar_event,
                    "usage": ai_result.get("usage") if ai_result else None
                }
            }

        except Exception as e:
            logger.error(f"AI 대화 시작 실패: {str(e)}")
            return {"status": 500, "error": f"오류: {str(e)}"}

    @staticmethod
    async def get_friend_conversation(user_id: str, friend_id: str) -> Dict[str, Any]:
        """특정 친구와의 대화 내용 조회"""
        try:
            messages = await ChatRepository.get_friend_messages(user_id, friend_id)

            # 메시지들을 시간순으로 정렬해서 대화 형태로 변환
            conversation = []
            for msg in messages:
                if msg.get("request_text"):
                    conversation.append({
                        "type": "user",
                        "message": msg["request_text"],
                        "timestamp": msg["created_at"]
                    })
                if msg.get("response_text"):
                    conversation.append({
                        "type": "ai",
                        "message": msg["response_text"],
                        "timestamp": msg["created_at"]
                    })

            return {
                "status": 200,
                "data": {
                    "friend_id": friend_id,
                    "messages": conversation
                }
            }

        except Exception as e:
            return {
                "status": 500,
                "error": f"친구 대화 조회 실패: {str(e)}"
            }

    @staticmethod
    async def _get_conversation_history(
        user_id: str,
        session_id: Optional[str] = None,        # ✅ 세션 옵션 추가
    ) -> List[Dict[str, str]]:
        """사용자의 최근 대화 히스토리 가져오기 (옵션: 특정 세션만)"""
        try:
            # 최근 30개의 대화 로그 가져오기 (거절 맥락 포함을 위해 증가)
            recent_logs = await ChatRepository.get_recent_chat_logs(
                user_id,
                limit=30,
                session_id=session_id,            # ✅ 세션 기준으로 조회
            )

            conversation_history: List[Dict[str, str]] = []
            for log in recent_logs:
                # 사용자 메시지
                if log.get("request_text"):
                    # 승인/거절 응답인 경우 맥락을 포함한 메시지로 변환
                    if log.get("message_type") == "schedule_approval_response":
                        metadata = log.get("metadata", {})
                        approved = metadata.get("approved", True)
                        proposal = metadata.get("proposal", {})

                        if approved:
                            conversation_history.append({
                                "type": "user",
                                "message": f"일정을 승인했습니다: {proposal.get('date', '')} {proposal.get('time', '')}"
                            })
                        else:
                            conversation_history.append({
                                "type": "user",
                                "message": (
                                    f"일정을 거절했습니다: "
                                    f"{proposal.get('date', '')} {proposal.get('time', '')}. "
                                    "다른 시간으로 재조율을 원합니다."
                                )
                            })
                    else:
                        # 일반 사용자 메시지
                        conversation_history.append({
                            "type": "user",
                            "message": log["request_text"]
                        })

                # AI 응답
                if log.get("response_text"):
                    conversation_history.append({
                        "type": "assistant",
                        "message": log["response_text"]
                    })

            return conversation_history

        except Exception as e:
            logger.error(f"대화 히스토리 조회 실패: {str(e)}")
            return []


    @staticmethod
    async def _find_friend_id_by_name(user_id: str, friend_name: str) -> str:
        """친구 이름으로 친구 ID 찾기 (개선된 매칭 알고리즘)"""
        try:
            if not friend_name or not friend_name.strip():
                return None

            # 1) 사용자의 친구 목록 조회 (friend_id만)
            friends_data = await ChatRepository.get_friends_list(user_id)
            friend_ids = [f.get("friend_id") for f in friends_data if f.get("friend_id")]
            if not friend_ids:
                logger.warning(f"친구 목록이 비어있음: user_id={user_id}")
                return None

            # 2) ID → 이름 매핑 조회
            id_to_name = await ChatRepository.get_user_names_by_ids(friend_ids)
            if not id_to_name:
                logger.warning(f"친구 이름 매핑 실패: friend_ids={friend_ids}")
                return None

            # 3) 강화된 이름 정규화 및 매칭
            def normalize(s: str) -> str:
                """이름 정규화: 공백 제거, 소문자 변환, 특수문자 제거"""
                if not s:
                    return ""
                # 공백 제거, 소문자 변환
                normalized = s.strip().lower().replace(" ", "").replace("-", "")
                # 한글 자음/모음 제거하지 않고 그대로 반환
                return normalized

            def similarity_score(name1: str, name2: str) -> float:
                """두 이름의 유사도 점수 계산 (0.0 ~ 1.0)"""
                n1 = normalize(name1)
                n2 = normalize(name2)

                if n1 == n2:
                    return 1.0

                # 완전 포함 관계 (긴 이름에 짧은 이름이 포함되는 경우)
                if len(n1) > len(n2):
                    if n2 in n1:
                        # 짧은 이름이 긴 이름의 시작 부분과 일치하는 경우 더 높은 점수
                        if n1.startswith(n2):
                            return 0.9
                        return 0.7
                elif len(n2) > len(n1):
                    if n1 in n2:
                        if n2.startswith(n1):
                            return 0.9
                        return 0.7

                # 공통 문자 비율 계산 (더 정교하게)
                common = set(n1) & set(n2)
                if not common:
                    return 0.0

                # 길이 차이가 크면 점수 감소
                length_diff = abs(len(n1) - len(n2))
                if length_diff > 2:
                    return 0.3

                return len(common) / max(len(n1), len(n2))

            target = normalize(friend_name)
            # logger.info(f"친구 이름 검색: '{friend_name}' (정규화: '{target}'), 후보: {list(id_to_name.values())}")

            # 우선순위 1: 완전 일치
            for fid, name in id_to_name.items():
                if normalize(name) == target:
                    # logger.info(f"완전 일치 발견: {name} (id: {fid})")
                    return fid

            # 우선순위 2: 시작 부분 일치 (더 정확한 매칭)
            # "성신조이"를 찾을 때 "성신조"가 아닌 "성신조이"를 우선 매칭
            for fid, name in id_to_name.items():
                norm_name = normalize(name)
                # 입력 이름이 DB 이름의 시작 부분과 일치하는 경우
                if norm_name.startswith(target) and len(norm_name) >= len(target):
                    logger.info(f"시작 부분 일치 발견: {name} (id: {fid})")
                    return fid
                # DB 이름이 입력 이름의 시작 부분과 일치하는 경우
                if target.startswith(norm_name) and len(target) >= len(norm_name):
                    logger.info(f"시작 부분 일치 발견: {name} (id: {fid})")
                    return fid

            # 우선순위 3: 포함 관계 (긴 이름에 짧은 이름이 포함)
            for fid, name in id_to_name.items():
                norm_name = normalize(name)
                if target in norm_name or norm_name in target:
                    logger.info(f"포함 일치 발견: {name} (id: {fid})")
                    return fid

            # 우선순위 4: 유사도 기반 매칭 (0.7 이상, 더 엄격하게)
            best_match = None
            best_score = 0.0
            for fid, name in id_to_name.items():
                score = similarity_score(friend_name, name)
                if score > best_score:
                    best_score = score
                    best_match = fid
                    logger.debug(f"유사도 매칭: {name} (id: {fid}, score: {score:.2f})")

            if best_score >= 0.7:
                matched_name = id_to_name.get(best_match, "알 수 없음")
                logger.info(f"유사도 매칭 성공: {matched_name} (id: {best_match}, score: {best_score:.2f})")
                return best_match

            logger.warning(f"친구 이름 매칭 실패: '{friend_name}' (최고 점수: {best_score:.2f})")
            return None

        except Exception as e:
            logger.error(f"친구 ID 검색 실패: {str(e)}", exc_info=True)
            return None

    @staticmethod
    async def _add_schedule_to_calendar(user_id: str, schedule_info: dict, original_text: str = "") -> dict | None:
        """일정 정보를 캘린더에 추가"""
        try:
            from src.calendar.calender_service import CalendarService

            # 날짜 파싱
            date_str = schedule_info.get("date", "")
            time_str = schedule_info.get("time", "")
            # [수정] activity를 가져올 때 기본값을 제거하여 None 체크 가능하도록 변경
            activity = schedule_info.get("activity")
            location = schedule_info.get("location", "")
            friend_name = schedule_info.get("friend_name", "")

            # [Safety Check] IntentService가 실패했을 경우를 대비해 여기서도 체크
            if "내일" in original_text and "내일" not in date_str:
                date_str = "내일"
                schedule_info["date"] = "내일"

            # [DEBUG] 날짜/시간 파싱 상세 로깅
            try:
                with open("debug_log.txt", "a") as f:
                    f.write(f"\n[DEBUG] === Schedule Creation Start ===\n")
                    f.write(f"[DEBUG] original_text: {original_text}\n")
                    f.write(f"[DEBUG] initial date_str: {schedule_info.get('date')}\n")
                    f.write(f"[DEBUG] resolved date_str: {date_str}\n")
            except:
                pass

            # 날짜 계산
            start_date = ChatService._parse_date(date_str)
            
            try:
                with open("debug_log.txt", "a") as f:
                    f.write(f"[DEBUG] parsed start_date: {start_date}\n")
            except:
                pass

            if not start_date:
                return None

            # [✅ FIX] schedule_info에 명시적인 start_time/end_time이 있으면 우선 사용
            explicit_start_time = schedule_info.get("start_time")
            explicit_end_time = schedule_info.get("end_time")
            
            if explicit_start_time:
                # HH:MM 형식을 datetime으로 변환
                try:
                    from zoneinfo import ZoneInfo
                    KST = ZoneInfo("Asia/Seoul")
                    time_parts = explicit_start_time.split(":")
                    start_time = start_date.replace(hour=int(time_parts[0]), minute=int(time_parts[1]) if len(time_parts) > 1 else 0, second=0)
                    
                    if explicit_end_time:
                        end_parts = explicit_end_time.split(":")
                        end_time = start_date.replace(hour=int(end_parts[0]), minute=int(end_parts[1]) if len(end_parts) > 1 else 0, second=0)
                        logger.info(f"[캘린더] 명시적 시간 사용: {start_time} ~ {end_time}")
                    else:
                        end_time = start_time + timedelta(hours=1)  # 기본 1시간
                        logger.info(f"[캘린더] 명시적 시작 시간 + 1시간: {start_time} ~ {end_time}")
                except Exception as e:
                    logger.warning(f"명시적 시간 파싱 실패: {e}, 기존 방식 사용")
                    start_time, end_time = ChatService._parse_time(schedule_info.get("time"), start_date, context_text=original_text)
            else:
                # 기존 방식: time 필드에서 파싱
                start_time, end_time = ChatService._parse_time(schedule_info.get("time"), start_date, context_text=original_text)
            
            try:
                with open("debug_log.txt", "a") as f:
                    f.write(f"[DEBUG] parsed start_time: {start_time}\n")
                    f.write(f"[DEBUG] parsed end_time: {end_time}\n")
            except:
                pass

            # [수정] 종료 시간이 명시되지 않은 경우(start==end), 최소 1시간 추가 (Google Calendar API는 0분 일정 허용 안 함)
            if start_time == end_time:
                end_time = start_time + timedelta(hours=1)
                logger.info(f"종료 시간 미지정 → 1시간 추가: {start_time} ~ {end_time}")

            # [수정] 일정 제목 생성 로직 개선 (title -> activity -> original_text)
            title = schedule_info.get("title")
            
            # [Safety Check] Title이 없는 경우 여기서 다시 추출 시도
            if not title:
                # 패턴 기반 추출 (하드코딩 제거)
                title_pattern = r"([가-힣A-Za-z0-9]+)\s*(예약|약속|미팅|모임|회식|진료|방문)"
                matches = re.finditer(title_pattern, original_text)
                for m in matches:
                    word = m.group(1)
                    type_ = m.group(2)
                    if word in ["오늘", "내일", "모레", "이번주", "다음주", "점심", "저녁", "아침", "새벽", "오후", "오전"]:
                        continue
                    title = f"{word} {type_}"
                    logger.info(f"ChatService Safety: Title 추출 성공 '{title}'")
                    break
            if title:
                summary = title
            elif activity:
                if friend_name:
                    summary = f"{friend_name}와 {activity}"
                else:
                    summary = activity
            else:
                # activity가 감지되지 않은 경우, 사용자의 원래 질문을 제목으로 사용
                summary = original_text if original_text else "일정"

            # 일정 설명 생성 (설명, 친구)
            description = "AI Assistant가 추가한 일정"
            if friend_name:
                description += f"\n친구: {friend_name}"

            # [NEW] 중복 일정 체크 (개인 일정만 - 친구가 없는 경우)
            if not friend_name:
                try:
                    from src.auth.auth_service import AuthService
                    from src.calendar.calender_service import GoogleCalendarService
                    
                    user_info = await AuthService.get_user_by_id(user_id)
                    if user_info and user_info.get("access_token"):
                        google_calendar = GoogleCalendarService()
                        
                        # [FIX] 해당 날짜의 전체 일정 조회 (종일 이벤트 포함)
                        day_start = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
                        day_end = start_time.replace(hour=23, minute=59, second=59, microsecond=0)
                        
                        existing_events = await google_calendar.get_calendar_events(
                            access_token=user_info["access_token"],
                            time_min=day_start,
                            time_max=day_end
                        )
                        
                        # 실제로 시간이 겹치는 이벤트만 필터링
                        conflicting_events = []
                        for evt in existing_events:
                            # 이벤트 시작/종료 시간 추출
                            evt_start_str = evt.start.get("dateTime") or evt.start.get("date")
                            evt_end_str = evt.end.get("dateTime") or evt.end.get("date")
                            
                            if evt_start_str and evt_end_str:
                                try:
                                    # 종일 이벤트 (date 형식)
                                    if "T" not in evt_start_str:
                                        # 종일 이벤트는 해당 날짜 전체를 차지함 - 무조건 충돌
                                        conflicting_events.append(evt)
                                    else:
                                        # 시간 이벤트 - 실제 겹침 확인
                                        from datetime import datetime
                                        evt_start_dt = datetime.fromisoformat(evt_start_str.replace("Z", "+00:00"))
                                        evt_end_dt = datetime.fromisoformat(evt_end_str.replace("Z", "+00:00"))
                                        
                                        # 새 일정과 기존 일정이 겹치는지 확인
                                        # (새 시작 < 기존 끝) AND (새 끝 > 기존 시작)
                                        if start_time < evt_end_dt and end_time > evt_start_dt:
                                            conflicting_events.append(evt)
                                except Exception as parse_err:
                                    logger.warning(f"이벤트 시간 파싱 실패: {parse_err}")
                        
                        if conflicting_events:
                            # 중복 일정 발견 - 생성하지 않고 충돌 정보 반환
                            conflict_names = [e.summary for e in conflicting_events[:3]]
                            logger.warning(f"중복 일정 발견: {conflict_names}")
                            
                            # 사용자 이름 가져오기
                            user_name = user_info.get("name") or user_info.get("username") or "회원"
                            
                            return {
                                "conflict": True,
                                "message": f"{user_name}님은 그 시간에 '{', '.join(conflict_names)}' 일정이 있어요. 다른 시간을 선택해 주세요!",
                                "existing_events": conflict_names
                            }
                except Exception as e:
                    logger.warning(f"중복 체크 중 오류 (무시하고 진행): {e}")

            # 캘린더에 일정 추가
            event_data = {
                "summary": summary,
                "description": description,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "location": location
            }

            calendar_result = await CalendarService.create_event(user_id, event_data)

            if calendar_result.get("status") == 200:
                logger.info(f"일정 추가 성공: {user_id} - {summary}")
                return {
                    "summary": summary,
                    "start_time": start_time.strftime("%Y-%m-%d %H:%M"),
                    "end_time": end_time.strftime("%Y-%m-%d %H:%M"),
                    "start_time_kst": start_time.strftime("%Y-%m-%d %H:%M"),
                    "location": location,
                    "google_event_id": calendar_result.get("data", {}).get("id")
                }
            else:
                logger.error(f"일정 추가 실패: {calendar_result.get('error')}")
                return None

        except Exception as e:
            logger.error(f"일정 추가 중 오류: {str(e)}")
            return None

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """날짜 문자열을 datetime으로 파싱"""
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")
        today = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        s = date_str.strip()

        # 상대 날짜
        if "오늘" in s:
            return today
        if "내일" in s:
            return today + timedelta(days=1)
        if "모레" in s:
            return today + timedelta(days=2)
        if "다음주" in s:
            return today + timedelta(days=7)
        if "이번주" in s:
            # 이번 주 토요일(또는 요구사항에 맞게 특정 요일)
            days_until_sat = (5 - today.weekday()) % 7
            return today + timedelta(days=days_until_sat)

        # 특정 날짜: "M월 D일" 또는 "D일"
        m_md = re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일', s)
        if m_md:
            month, day = int(m_md.group(1)), int(m_md.group(2))
            year = today.year
            candidate = datetime(year, month, day, tzinfo=KST)
            # 과거면 내년으로 롤오버
            if candidate < today:
                candidate = datetime(year + 1, month, day, tzinfo=KST)
            return candidate

        m_d = re.search(r'(\d{1,2})\s*일', s)
        if m_d:
            day = int(m_d.group(1))
            year, month = today.year, today.month
            candidate = datetime(year, month, day, tzinfo=KST)
            # 과거면 다음달로 롤오버
            if candidate < today:
                if month == 12:
                    candidate = datetime(year + 1, 1, day, tzinfo=KST)
                else:
                    candidate = datetime(year, month + 1, day, tzinfo=KST)
            return candidate

        # 미지정: 합리적 디폴트(내일)
        return today + timedelta(days=1)

    @staticmethod
    def _parse_time(time_str: str, date: datetime, context_text: str = "") -> tuple[datetime, datetime]:
        """시간 문자열을 시작/종료 시간으로 파싱"""
        KST = ZoneInfo("Asia/Seoul")
        t = (time_str or "").strip()
        ctx = f"{t} {context_text or ''}"

        # PM/AM 인디케이터 집합
        pm_words = ["오후", "저녁", "밤", "낮", "점심"]
        am_words = ["오전", "아침", "새벽"]

        def has_pm(text: str) -> bool:
            return any(w in text for w in pm_words)

        def has_am(text: str) -> bool:
            return any(w in text for w in am_words)

        def parse_hour(hh: int, context: str) -> int:
            """시간을 24시간 형식으로 변환"""
            # 명시적으로 오후/저녁이면 12 더함
            if has_pm(context) and 1 <= hh <= 11:
                hh += 12
            # 명시적으로 오전/아침이면 그대로 (12시는 0시로)
            elif has_am(context):
                if hh == 12:
                    hh = 0
            # AM/PM 미지정일 때: 1-6시는 보통 오후를 의미 (새벽 약속은 드묾)
            elif 1 <= hh <= 6:
                hh += 12
            return hh

        # 1) 시간 범위 파싱: "오후 7시부터 9시까지" 또는 "7시-9시" 등
        logger.info(f"시간 범위 파싱 시도: ctx='{ctx}'")

        # "오후 7시부터 9시까지" 형식
        m = re.search(r"오후\s*(\d{1,2})\s*시\s*부터\s*(\d{1,2})\s*시", ctx)
        if m:
            start_hh = int(m.group(1)) + 12
            end_hh = int(m.group(2)) + 12
            logger.info(f"오후 시간 범위 매칭: start_hh={start_hh}, end_hh={end_hh}")
            start = date.replace(hour=start_hh, minute=0, second=0, microsecond=0, tzinfo=KST)
            end = date.replace(hour=end_hh, minute=0, second=0, microsecond=0, tzinfo=KST)
            return start, end

        # "오전 7시부터 9시까지" 형식
        m = re.search(r"오전\s*(\d{1,2})\s*시\s*부터\s*(\d{1,2})\s*시", ctx)
        if m:
            start_hh = int(m.group(1))
            end_hh = int(m.group(2))
            logger.info(f"오전 시간 범위 매칭: start_hh={start_hh}, end_hh={end_hh}")
            start = date.replace(hour=start_hh, minute=0, second=0, microsecond=0, tzinfo=KST)
            end = date.replace(hour=end_hh, minute=0, second=0, microsecond=0, tzinfo=KST)
            return start, end

        # "7시부터 9시까지" 형식 (AM/PM 없음)
        m = re.search(r"(\d{1,2})\s*시\s*부터\s*(\d{1,2})\s*시", ctx)
        if m:
            start_hh = int(m.group(1))
            end_hh = int(m.group(2))
            # 12시 이하는 오후로 가정
            if start_hh <= 12:
                start_hh += 12
            if end_hh <= 12:
                end_hh += 12
            logger.info(f"시간 범위 매칭 (AM/PM 없음): start_hh={start_hh}, end_hh={end_hh}")
            start = date.replace(hour=start_hh, minute=0, second=0, microsecond=0, tzinfo=KST)
            end = date.replace(hour=end_hh, minute=0, second=0, microsecond=0, tzinfo=KST)
            return start, end

        # 2) 단일 시간 파싱: hh:mm
        m = re.search(r"(\d{1,2}):(\d{2})", t)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            hh = parse_hour(hh, ctx)
            start = date.replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=KST)
            return start, start

        # 3) 단일 시간 파싱: N시(분 포함 또는 반)
        m = re.search(r"(\d{1,2})\s*시(?:\s*반)?(?:\s*(\d{1,2})\s*분)?", t)
        if m:
            hh = int(m.group(1))
            # "반" 처리 (30분)
            if "반" in t:
                mm = 30
            elif m.group(2):
                mm = int(m.group(2))
            else:
                mm = 0
            hh = parse_hour(hh, ctx)
            start = date.replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=KST)
            return start, start

        # 4) 수식어만 있을 때 기본값
        if "새벽" in ctx:
            hh = 2
        elif ("아침" in ctx) or ("오전" in ctx):
            hh = 9
        elif "점심" in ctx:
            hh = 12
        elif any(w in ctx for w in ["저녁", "오후", "밤", "낮"]):
            hh = 18
        else:
            hh = 14
        start = date.replace(hour=hh, minute=0, second=0, microsecond=0, tzinfo=KST)
        return start, start

    @staticmethod
    async def parse_time_string(time_str: str, context_text: str = "") -> Optional[Dict[str, Any]]:
        """
        시간 문자열을 파싱하여 start_time, end_time을 반환 (외부 호출용)
        """
        try:
            # 날짜 추출 (문맥에서 날짜 정보가 있다면 활용)
            start_date = ChatService._parse_date(context_text)
            if not start_date:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                KST = ZoneInfo("Asia/Seoul")
                start_date = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)

            # 시간 파싱
            start_time, end_time = ChatService._parse_time(time_str, start_date, context_text)

            # 기본 1시간 설정 (시작/종료 시간이 같은 경우)
            if start_time == end_time:
                end_time = start_time + timedelta(hours=1)

            return {
                "start_time": start_time,
                "end_time": end_time
            }
        except Exception as e:
            logger.error(f"시간 파싱 실패: {str(e)}")
            return None
