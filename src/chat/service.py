from typing import List, Dict, Any
from zoneinfo import ZoneInfo

from .repository import ChatRepository
from .models import ChatRoom, ChatMessage, ChatRoomListResponse, ChatMessagesResponse
from .openai_service import OpenAIService
from collections import defaultdict
import uuid
import logging
from datetime import datetime, timedelta
import re
from src.intent.service import IntentService

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
            
            # 일정 정보 추출 (Intent 모듈로 분리)
            schedule_info = await IntentService.extract_schedule_info(message)
            friend_names_list = schedule_info.get("friend_names")  # 여러 명
            friend_name = schedule_info.get("friend_name") if schedule_info.get("has_schedule_request") else None
            
            # 여러 친구 이름이 있으면 리스트로, 없으면 단일 이름으로 처리
            if friend_names_list and len(friend_names_list) > 1:
                friend_names = friend_names_list
            elif friend_name:
                friend_names = [friend_name]
            else:
                friend_names = []
            
            logger.info(f"[CHAT] schedule_info: has={schedule_info.get('has_schedule_request')}, friends={friend_names}, intent={schedule_info.get('intent')}")
            
            # 여러 친구 ID 찾기
            friend_ids = []
            friend_id_to_name = {}
            for name in friend_names:
                fid = await ChatService._find_friend_id_by_name(user_id, name)
                if fid:
                    friend_ids.append(fid)
                    friend_id_to_name[fid] = name
                    logger.info(f"[CHAT] friend_id lookup result for '{name}': {fid}")
            
            # 일정 요청이 감지되고 친구 ID가 있으면 A2A 세션 자동 시작
            a2a_session_id = None
            if schedule_info.get("has_schedule_request") and friend_ids:
                try:
                    from src.a2a.service import A2AService
                    # 요약 메시지 생성
                    summary_parts = []
                    if friend_names:
                        if len(friend_names) > 1:
                            summary_parts.append(", ".join(friend_names[:-1]) + f"와 {friend_names[-1]}")
                        else:
                            summary_parts.append(friend_names[0])
                    if schedule_info.get("date"):
                        summary_parts.append(schedule_info.get("date"))
                    if schedule_info.get("time"):
                        summary_parts.append(schedule_info.get("time"))
                    summary = " ".join(summary_parts) if summary_parts else "약속"
                    
                    # A2A 세션 시작 (다중 사용자 지원)
                    a2a_result = await A2AService.start_multi_user_session(
                        initiator_user_id=user_id,
                        target_user_ids=friend_ids,
                        summary=summary,
                        date=schedule_info.get("date"),
                        time=schedule_info.get("time"),
                        location=schedule_info.get("location"),
                        activity=schedule_info.get("activity"),
                        duration_minutes=60
                    )
                    
                    if a2a_result.get("status") == 200:
                        thread_id = a2a_result.get("thread_id")
                        session_ids = a2a_result.get("session_ids", [])
                        needs_approval = a2a_result.get("needs_approval", False)
                        proposal = a2a_result.get("proposal")
                        
                        if needs_approval and proposal:
                            # 승인 필요: 사용자에게 확정 제안
                            date_str = proposal.get("date", "")
                            time_str = proposal.get("time", "")
                            location_str = proposal.get("location", "")
                            participants_str = ", ".join(proposal.get("participants", []))
                            
                            ai_response = f"✅ 약속 확정: {date_str} {time_str}"
                            if location_str:
                                ai_response += f" / {location_str}"
                            ai_response += f"\n참여자: {participants_str}\n확정하시겠습니까?"
                            
                            # schedule_info에 승인 필요 정보 추가
                            schedule_info["needs_approval"] = True
                            schedule_info["proposal"] = proposal
                            schedule_info["thread_id"] = thread_id
                            schedule_info["session_ids"] = session_ids
                        else:
                            # A2A 세션이 성공적으로 시작되었음을 알리는 응답
                            if len(friend_names) > 1:
                                ai_response = f"🤖 {', '.join(friend_names)}님들의 Agent와 일정을 조율하고 있습니다. A2A 화면에서 진행 상황을 확인하실 수 있습니다."
                            else:
                                ai_response = f"🤖 {friend_names[0]}님의 Agent와 일정을 조율하고 있습니다. A2A 화면에서 진행 상황을 확인하실 수 있습니다."
                        
                        logger.info(f"A2A 세션 시작 성공: thread_id={thread_id}, session_ids={session_ids}, needs_approval={needs_approval}")
                    else:
                        # A2A 세션 시작 실패 시 기존 로직으로 폴백
                        error_msg = a2a_result.get('error', '알 수 없는 오류')
                        logger.warning(f"A2A 세션 시작 실패: {error_msg}")
                        ai_response = f"일정 조율을 시도했지만 문제가 발생했습니다: {error_msg}. 다시 시도해주세요."
                except Exception as e:
                    logger.error(f"A2A 세션 시작 중 오류: {str(e)}")
                    # 오류 발생 시 기존 로직으로 폴백
                    ai_response = "일정 조율을 시도했지만 문제가 발생했습니다. 다시 시도해주세요."
            
            # A2A 세션이 시작되지 않은 경우에만 기존 일정 추가 로직 실행
            calendar_event = None
            if not a2a_session_id and schedule_info.get("has_schedule_request") and schedule_info.get("date") and schedule_info.get("time"):
                calendar_event = await ChatService._add_schedule_to_calendar(user_id, schedule_info, original_text=message)

                if calendar_event:
                    start_str = (
                            calendar_event.get("start_time_kst")
                            or calendar_event.get("start_time")
                            or schedule_info.get("time")  # 마지막 안전망
                            or ""
                    )
                    # ✅ LLM 원문에 덧붙이지 말고, 아예 성공 카드로 교체
                    ai_response = (
                        "✅ 일정이 성공적으로 추가되었습니다!\n"
                        f"📅 {calendar_event.get('summary', '새 일정')}\n"
                        f"🕐 {calendar_event.get('start_time_kst', '')}\n"
                        f"📍 {calendar_event.get('location', '')}"
                    )
            # 사용자 메시지 저장 (여러 친구인 경우 friend_id는 None)
            # 여러 친구와의 일정은 A2A 세션으로 처리되므로 friend_id는 첫 번째 친구 또는 None
            first_friend_id = friend_ids[0] if friend_ids else None
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=message,
                response_text=None,
                friend_id=first_friend_id if len(friend_ids) == 1 else None,  # 여러 명이면 None
                message_type="user_message"
            )
            
            # AI 응답 저장
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=None,
                response_text=ai_response,
                friend_id=first_friend_id if len(friend_ids) == 1 else None,  # 여러 명이면 None
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
            logger.info(f"친구 이름 검색: '{friend_name}' (정규화: '{target}'), 후보: {list(id_to_name.values())}")

            # 우선순위 1: 완전 일치
            for fid, name in id_to_name.items():
                if normalize(name) == target:
                    logger.info(f"완전 일치 발견: {name} (id: {fid})")
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
            from src.calendar.service import CalendarService
            
            # 날짜 파싱
            date_str = schedule_info.get("date", "")
            time_str = schedule_info.get("time", "")
            activity = schedule_info.get("activity", "일정")
            location = schedule_info.get("location", "")
            friend_name = schedule_info.get("friend_name", "")
            
            # 날짜 계산
            start_date = ChatService._parse_date(schedule_info.get("date"))
            if not start_date:
                return None
            
            # 시간 계산
            logger.info(f"시간 파싱 시작: time_str='{schedule_info.get('time')}', context='{original_text}'")
            start_time, end_time = ChatService._parse_time(schedule_info.get("time"), start_date, context_text=original_text)
            logger.info(f"시간 파싱 결과: start_time={start_time}, end_time={end_time}")
            
            # 일정 제목 생성 (친구가 있으면 친구와 함께, 없으면 활동만)
            summary = activity
            if friend_name:
                summary = f"{friend_name}와 {activity}"
            
            # 일정 설명 생성 (설명, 친구)
            description = "AI Assistant가 추가한 일정"
            if friend_name:
                description += f"\n친구: {friend_name}"
            
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
        if "오늘" in s: return today
        if "내일" in s: return today + timedelta(days=1)
        if "모레" in s: return today + timedelta(days=2)
        if "다음주" in s: return today + timedelta(days=7)
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
            if candidate < today: candidate = datetime(year + 1, month, day, tzinfo=KST)
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
            if has_pm(context) and 1 <= hh <= 11:
                hh += 12
            if has_am(context) and hh == 12:
                hh = 0
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

        # 3) 단일 시간 파싱: N시(분 포함)
        m = re.search(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", t)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2)) if m.group(2) else 0
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