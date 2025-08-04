from typing import List, Dict, Any
from .repository import ChatRepository
from .models import ChatRoom, ChatMessage, ChatRoomListResponse, ChatMessagesResponse
from collections import defaultdict
import uuid

class ChatService:
    
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
        """두 사용자 간의 채팅 메시지 조회"""
        try:
            messages_data = await ChatRepository.get_chat_messages(user_id, other_user_id)
            
            messages = [
                ChatMessage(
                    id=msg['id'],
                    send_id=msg['send_id'],
                    receive_id=msg['receive_id'],
                    message=msg['message'],
                    message_type=msg.get('message_type', 'text'),
                    created_at=msg['created_at']
                )
                for msg in messages_data
            ]
            
            return {
                "status": 200,
                "data": ChatMessagesResponse(messages=messages)
            }
            
        except Exception as e:
            return {
                "status": 500,
                "error": f"채팅 메시지 조회 실패: {str(e)}"
            }
    
    @staticmethod
    async def send_message(send_id: str, receive_id: str, message: str, message_type: str = "text") -> Dict[str, Any]:
        """메시지 전송"""
        try:
            # 메시지 전송
            sent_message = await ChatRepository.send_message(send_id, receive_id, message, message_type)
            
            message_obj = ChatMessage(
                id=sent_message['id'],
                send_id=sent_message['send_id'],
                receive_id=sent_message['receive_id'],
                message=sent_message['message'],
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
    async def start_ai_conversation(user_id: str, message: str) -> Dict[str, Any]:
        """AI와 일정 조율 대화 시작"""
        try:
            # 간단한 AI 응답 로직 (실제로는 더 복잡한 AI 처리)
            ai_response = await ChatService._process_ai_request(user_id, message)
            
            # 친구 정보 추출 (AI가 친구를 찾은 경우)
            session_info = ai_response.get("session_info")
            friend_name = session_info.get("friend_name") if session_info else None
            
            # 친구 ID 찾기 (실제로는 friend_list에서 찾아야 함, 여기서는 간단히 처리)
            friend_id = None
            if friend_name:
                # TODO: friend_list 테이블에서 friend_name으로 friend_id 찾기
                pass
            
            # 사용자 메시지 저장
            user_log = await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=message,
                response_text=None,
                friend_id=friend_id,
                message_type="user_request"
            )
            
            # AI 응답 저장
            ai_log = await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=None,
                response_text=ai_response["message"],
                friend_id=friend_id,
                message_type="ai_response"
            )
            
            return {
                "status": 200,
                "data": {
                    "user_message": message,
                    "ai_response": ai_response["message"],
                    "session_info": ai_response.get("session_info")
                }
            }
            
        except Exception as e:
            return {
                "status": 500,
                "error": f"AI 대화 시작 실패: {str(e)}"
            }
    
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
    async def _process_ai_request(user_id: str, message: str) -> Dict[str, Any]:
        """AI 요청 처리 (간단한 버전)"""
        import re
        
        # 간단한 패턴 매칭으로 친구 이름과 일정 추출
        # 예: "아구만이랑 내일 점심 약속 잡아줘"
        friend_pattern = r"(\w+)(?:이?랑|과|와)"
        schedule_pattern = r"(내일|모레|오늘|다음주|이번주)?\s*(\w+)\s*(약속|미팅|만남)"
        
        friend_match = re.search(friend_pattern, message)
        schedule_match = re.search(schedule_pattern, message)
        
        if friend_match and schedule_match:
            friend_name = friend_match.group(1)
            when = schedule_match.group(1) or "언제든"
            what = schedule_match.group(2)
            
            return {
                "message": f"네! {friend_name}님과 {when} {what} 일정을 조율해드리겠습니다. 잠시만 기다려 주세요...",
                "session_info": {
                    "friend_name": friend_name,
                    "when": when,
                    "what": what
                }
            }
        else:
            return {
                "message": "일정 조율을 도와드리겠습니다. 어떤 약속을 잡고 싶으신가요? (예: '아구만이랑 내일 점심 약속 잡아줘')",
                "session_info": None
            } 