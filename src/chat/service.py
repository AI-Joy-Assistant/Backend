from typing import List, Dict, Any
from .repository import ChatRepository
from .models import ChatRoom, ChatMessage, ChatRoomListResponse, ChatMessagesResponse
from .openai_service import OpenAIService
from collections import defaultdict
import uuid
import logging
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)

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
        """AI와 일정 조율 대화 시작 (ChatGPT API 사용)"""
        try:
            # OpenAI 서비스 초기화
            openai_service = OpenAIService()
            
            # 이전 대화 히스토리 가져오기
            conversation_history = await ChatService._get_conversation_history(user_id)
            
            # ChatGPT API로 응답 생성
            ai_result = await openai_service.generate_response(message, conversation_history)
            
            if ai_result["status"] == "error":
                return {
                    "status": 500,
                    "error": ai_result["message"]
                }
            
            ai_response = ai_result["message"]
            
            # 일정 정보 추출
            schedule_info = await openai_service.extract_schedule_info(message)
            friend_name = schedule_info.get("friend_name") if schedule_info.get("has_schedule_request") else None
            
            # 친구 ID 찾기
            friend_id = None
            if friend_name:
                friend_id = await ChatService._find_friend_id_by_name(user_id, friend_name)
            
            # 일정 추가 시도
            calendar_event = None
            if schedule_info.get("has_schedule_request") and schedule_info.get("date") and schedule_info.get("time"):
                logger.info(f"일정 추가 시도: {schedule_info}")
                calendar_event = await ChatService._add_schedule_to_calendar(user_id, schedule_info)
                if calendar_event:
                    logger.info(f"일정 추가 성공: {calendar_event}")
                    ai_response += f"\n\n✅ 일정이 성공적으로 추가되었습니다!\n📅 {calendar_event.get('summary', '새 일정')}\n🕐 {calendar_event.get('start_time', '')}"
                else:
                    logger.error(f"일정 추가 실패: calendar_event is None")
            else:
                logger.info(f"일정 추가 조건 불충족: has_schedule_request={schedule_info.get('has_schedule_request')}, date={schedule_info.get('date')}, time={schedule_info.get('time')}")
            
            # 사용자 메시지 저장
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=message,
                response_text=None,
                friend_id=friend_id,
                message_type="user_message"
            )
            
            # AI 응답 저장
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=None,
                response_text=ai_response,
                friend_id=friend_id,
                message_type="ai_response"
            )
            
            logger.info(f"AI 대화 완료 - 사용자: {user_id}, 토큰 사용량: {ai_result.get('usage', {})}")
            
            return {
                "status": 200,
                "data": {
                    "user_message": message,
                    "ai_response": ai_response,
                    "schedule_info": schedule_info,
                    "calendar_event": calendar_event,
                    "usage": ai_result.get("usage")
                }
            }
            
        except Exception as e:
            logger.error(f"AI 대화 시작 실패: {str(e)}")
            return {
                "status": 500,
                "error": f"일시적인 오류가 발생했습니다: {str(e)}"
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
    async def _get_conversation_history(user_id: str) -> List[Dict[str, str]]:
        """사용자의 최근 대화 히스토리 가져오기"""
        try:
            # 최근 20개의 대화 로그 가져오기
            recent_logs = await ChatRepository.get_recent_chat_logs(user_id, limit=20)
            
            conversation_history = []
            for log in recent_logs:
                if log.get("request_text"):
                    conversation_history.append({
                        "type": "user",
                        "message": log["request_text"]
                    })
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
        """친구 이름으로 친구 ID 찾기"""
        try:
            # 사용자의 친구 목록에서 이름으로 검색
            friends_data = await ChatRepository.get_friends_list(user_id)
            
            for friend in friends_data:
                # TODO: 실제로는 friend_list 테이블에서 friend_name 컬럼을 조회해야 함
                # 현재는 간단히 friend_id를 반환
                if friend.get("friend_id"):
                    return friend["friend_id"]
            
            return None
            
        except Exception as e:
            logger.error(f"친구 ID 검색 실패: {str(e)}")
            return None
    
    @staticmethod
    async def _add_schedule_to_calendar(user_id: str, schedule_info: Dict[str, Any]) -> Dict[str, Any]:
        """일정 정보를 캘린더에 추가"""
        try:
            from src.calendar.service import CalendarService
            
            # 날짜 파싱
            date_str = schedule_info.get("date", "")
            time_str = schedule_info.get("time", "")
            activity = schedule_info.get("activity", "일정")
            location = schedule_info.get("location", "")
            friend_name = schedule_info.get("friend_name", "")
            
            # 날짜 계산
            start_date = ChatService._parse_date(date_str)
            if not start_date:
                return None
            
            # 시간 계산
            start_time, end_time = ChatService._parse_time(time_str, start_date)
            
            # 일정 제목 생성 (친구가 있으면 친구와 함께, 없으면 활동만)
            summary = activity
            if friend_name:
                summary = f"{friend_name}와 {activity}"
            
            # 일정 설명 생성 (설명, 친구, 장소)
            description = "AI Assistant가 추가한 일정"
            if friend_name:
                description += f"\n친구: {friend_name}"
            if location:
                description += f"\n장소: {location}"
            
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
        today = datetime.now(KST)
        
        if "오늘" in date_str:
            return today
        elif "내일" in date_str:
            return today + timedelta(days=1)
        elif "모레" in date_str:
            return today + timedelta(days=2)
        elif "다음주" in date_str:
            return today + timedelta(days=7)
        elif "이번주" in date_str:
            # 이번 주 토요일
            days_until_saturday = (5 - today.weekday()) % 7
            return today + timedelta(days=days_until_saturday)
        else:
            # 특정 날짜 형식 (예: "8월 15일", "15일")
            try:
                if "월" in date_str and "일" in date_str:
                    # "8월 15일" 형식
                    month_match = re.search(r'(\d+)월', date_str)
                    day_match = re.search(r'(\d+)일', date_str)
                    if month_match and day_match:
                        month = int(month_match.group(1))
                        day = int(day_match.group(1))
                        year = today.year
                        return datetime(year, month, day, tzinfo=KST)
                elif "일" in date_str:
                    # "15일" 형식
                    day_match = re.search(r'(\d+)일', date_str)
                    if day_match:
                        day = int(day_match.group(1))
                        year = today.year
                        month = today.month
                        return datetime(year, month, day, tzinfo=KST)
            except:
                pass
            
            # 기본값: 내일
            return today + timedelta(days=1)
    
    @staticmethod
    def _parse_time(time_str: str, date: datetime) -> tuple[datetime, datetime]:
        """시간 문자열을 시작/종료 시간으로 파싱"""
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")
        text = (time_str or "").strip()

        def parse_single(phrase: str, default_meridiem: str | None = None) -> datetime:
            p = phrase.strip()
            meridiem = None
            if "오후" in p:
                meridiem = "pm"
            elif "오전" in p:
                meridiem = "am"
            elif default_meridiem:
                meridiem = default_meridiem

            # HH:MM 또는 HH시 MM분
            m = re.search(r"(\d{1,2})(?::(\d{1,2}))?", p)
            if not m:
                # 숫자가 없다면 의미 있는 디폴트로 처리
                if "점심" in p:
                    h, mm = 12, 0
                elif "저녁" in p:
                    h, mm = 18, 0
                elif "아침" in p:
                    h, mm = 9, 0
                elif meridiem == "pm":
                    h, mm = 14, 0
                elif meridiem == "am":
                    h, mm = 9, 0
                else:
                    h, mm = 14, 0
            else:
                h = int(m.group(1))
                mm = int(m.group(2) or 0)
                # 24시간 표기면 그대로, 12시간 표기면 오전/오후 적용
                if h <= 12 and meridiem:
                    if meridiem == "pm" and h < 12:
                        h += 12
                    if meridiem == "am" and h == 12:
                        h = 0

            return date.replace(hour=h, minute=mm, second=0, microsecond=0, tzinfo=KST)

        # 시간 범위: "...부터 ...까지", "...-...", "...~..."
        range_match = re.split(r"부터|[-~]|~|까지", text)
        if len([s for s in range_match if s.strip()]) >= 2:
            # 분리 시퀀스에서 앞 2개를 사용
            parts = [s for s in range_match if s.strip()]
            start_phrase, end_phrase = parts[0], parts[1]
            # 시작의 오전/오후 기준을 종료에도 상속
            start_meridiem = "pm" if "오후" in start_phrase else ("am" if "오전" in start_phrase else None)
            start_time = parse_single(start_phrase)
            end_time = parse_single(end_phrase, default_meridiem=start_meridiem)
            # 종료가 시작보다 같거나 빠르면 1시간 보정
            if end_time <= start_time:
                end_time = start_time + timedelta(hours=1)
        else:
            start_time = parse_single(text)
            end_time = start_time + timedelta(hours=1)

        return start_time, end_time