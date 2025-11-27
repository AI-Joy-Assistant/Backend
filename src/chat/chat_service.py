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
        """AI와 일정 조율 대화 시작"""
        try:
            # 1. 사용자 메시지 저장
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=message,
                response_text=None,
                friend_id=None,
                message_type="user_message"
            )

            # 2. 의도 파악
            schedule_info = await IntentService.extract_schedule_info(message)
            friend_names_list = schedule_info.get("friend_names")
            friend_name = schedule_info.get("friend_name") if schedule_info.get("has_schedule_request") else None

            if friend_names_list and len(friend_names_list) > 1:
                friend_names = friend_names_list
            elif friend_name:
                friend_names = [friend_name]
            else:
                friend_names = []

            logger.info(f"[CHAT] schedule_info: {schedule_info}")

            # [✅ 수정 1] 변수 초기화 (500 에러 방지)
            ai_result = {}
            ai_response = None
            openai_service = OpenAIService()

            recoordination_needed = False
            thread_id_for_recoordination = None
            session_ids_for_recoordination = []

            # --- 재조율 감지 로직 ---
            from config.database import supabase
            from datetime import datetime, timezone, timedelta

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
                            continue # 건너뜀
                    except:
                        pass

                    if not meta.get('approved', True) and meta.get('thread_id'):
                        # 거절 이력 발견
                        # 여기서 바로 True로 하지 않고, 이 거절 이후에 성공한 세션이 없는지 체크하면 더 좋지만 일단 간단하게 처리
                        if schedule_info.get("date") or schedule_info.get("time") or message.strip():
                            recoordination_needed = True
                            thread_id_for_recoordination = meta.get('thread_id')
                            session_ids_for_recoordination = meta.get('session_ids', [])
                            logger.info(f"재조율 감지 (사용자 거절): thread_id={thread_id_for_recoordination}")
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
                            if log_time < last_success_time: continue
                        except: pass

                        meta = log.get('metadata', {})
                        if meta.get('needs_recoordination') and meta.get('thread_id'):
                            if schedule_info.get("date") or schedule_info.get("time") or message.strip():
                                recoordination_needed = True
                                thread_id_for_recoordination = meta.get('thread_id')
                                session_ids_for_recoordination = meta.get('session_ids', [])
                                logger.info(f"재조율 감지 (시스템 거절 알림): thread_id={thread_id_for_recoordination}")
                                break

                # B. AI가 보낸 "재조율을 위해..." 메시지 확인 (ai_response)
                if not recoordination_needed:
                    ai_reject = supabase.table('chat_log').select('*').eq('user_id', user_id).eq('message_type', 'ai_response').order('created_at', desc=True).limit(3).execute()
                    if ai_reject.data:
                        for log in ai_reject.data:
                            # [✅ 추가 2] 시간 체크
                            try:
                                log_time = datetime.fromisoformat(log['created_at'].replace('Z', '+00:00'))
                                if log_time < last_success_time: continue
                            except: pass

                            meta = log.get('metadata', {})
                            # [핵심] 로그 상 'metadata': {'needs_recoordination': true, ...} 가 있는지 확인
                            if meta and meta.get('needs_recoordination') and meta.get('thread_id'):
                                if schedule_info.get("date") or schedule_info.get("time") or message.strip():
                                    recoordination_needed = True
                                    thread_id_for_recoordination = meta.get('thread_id')
                                    session_ids_for_recoordination = meta.get('session_ids', [])
                                    logger.info(f"재조율 감지 (AI 재조율 요청): thread_id={thread_id_for_recoordination}")
                                    break

            # [FIX] 명시적인 친구 이름이 감지되면 재조율 로직(과거 참여자 복구)을 무시하고 새로운 요청으로 처리
            # 이렇게 해야 "민서랑 성신조이랑"이라고 했을 때 과거의 "성신조이"만 있는 세션으로 돌아가지 않음
            if schedule_info.get("friend_names") or schedule_info.get("friend_name"):
                if recoordination_needed:
                    logger.info(f"명시적인 친구 이름({schedule_info.get('friend_names') or schedule_info.get('friend_name')})이 감지되어 재조율 모드를 해제합니다.")
                    recoordination_needed = False
                    thread_id_for_recoordination = None
                    session_ids_for_recoordination = []

            # [판단] 일정 요청이거나 재조율이면 -> AI 생성 스킵
            is_schedule_related = schedule_info.get("has_schedule_request") or recoordination_needed

            if not is_schedule_related:
                # 일반 대화
                conversation_history = await ChatService._get_conversation_history(user_id)
                ai_result = await openai_service.generate_response(message, conversation_history)
                if ai_result["status"] == "error":
                    return {"status": 500, "error": ai_result["message"]}
                ai_response = ai_result["message"]
            else:
                ai_response = None

                # 친구 ID 찾기
            friend_ids = []
            friend_id_to_name = {}

            if recoordination_needed:
                # [✅ 수정 2] 재조율 시 친구 정보 복구 확실하게 처리
                from src.a2a.a2a_repository import A2ARepository
                # session_ids가 있으면 그것으로, 없으면 thread_id로 찾기
                target_sessions = []
                # 1. session_ids로 조회 시도
                if session_ids_for_recoordination:
                    for sid in session_ids_for_recoordination:
                        sess = await A2ARepository.get_session(sid)
                        if sess: target_sessions.append(sess)

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
                        if s.get('initiator_user_id'): all_pids.add(s['initiator_user_id'])
                        if s.get('target_user_id'): all_pids.add(s['target_user_id'])

                    # 나(user_id) 제외
                    if user_id in all_pids:
                        all_pids.remove(user_id)

                    friend_ids = list(all_pids)

                    if friend_ids:
                        # 이름 조회
                        user_names = await ChatRepository.get_user_names_by_ids(friend_ids)
                        friend_id_to_name = {fid: user_names.get(fid, '사용자') for fid in friend_ids}
                        friend_names = [friend_id_to_name.get(fid, '사용자') for fid in friend_ids]
                        logger.info(f"재조율 참여자 복구 성공: {friend_names} (IDs: {friend_ids})")
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
                        message_type="ai_response"
                    )
                    response_sent_to_db = True
                    ai_response = wait_msg

                    # 요약 메시지
                    summary_parts = []
                    if friend_names:
                        summary_parts.append(", ".join(friend_names))
                    if schedule_info.get("date"): summary_parts.append(schedule_info.get("date"))
                    if schedule_info.get("time"): summary_parts.append(schedule_info.get("time"))
                    summary = " ".join(summary_parts) if summary_parts else "약속"

                    if recoordination_needed:
                        # [재조율 로직]
                        from src.auth.auth_repository import AuthRepository
                        user_info = await AuthRepository.find_user_by_id(user_id)
                        initiator_name = user_info.get("name", "사용자") if user_info else "사용자"

                        # 세션 상태 업데이트
                        for session_id in session_ids_for_recoordination:
                            await A2ARepository.update_session_status(session_id, "in_progress")

                        sessions_info = []
                        for session_id, friend_id in zip(session_ids_for_recoordination, friend_ids):
                            sessions_info.append({
                                "session_id": session_id,
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
                        thread_id = a2a_result.get("thread_id")
                        session_ids = a2a_result.get("session_ids", [])

                    # 결과 처리
                    needs_approval = a2a_result.get("needs_approval", False)
                    proposal = a2a_result.get("proposal")

                    if (recoordination_needed or a2a_result.get("status") == 200):
                        if needs_approval and proposal:
                            date_str = proposal.get("date", "")
                            time_str = proposal.get("time", "")
                            confirm_msg = f"✅ 약속 확정: {date_str} {time_str}\n확정하시겠습니까?"
                            ai_response = confirm_msg
                        elif a2a_result.get("needs_recoordination"):
                            # [FIX] a2a_service에서 이미 충돌 알림 메시지를 DB에 저장했으므로
                            # 여기서 또 ai_response로 반환하면 프론트엔드에서 중복으로 표시됨 (폴링 + 로컬 추가)
                            # 따라서 여기서는 ai_response를 비워서 중복 방지
                            ai_response = None

                except Exception as e:
                    logger.error(f"A2A 세션 시작 중 오류: {str(e)}")
                    ai_response = "일정 조율을 시도했지만 문제가 발생했습니다."
                    if response_sent_to_db:
                        await ChatRepository.create_chat_log(user_id=user_id, response_text=ai_response, message_type="ai_response")
                    else:
                        response_sent_to_db = False

            # 5. 캘린더 직접 추가 (A2A가 아닐 때만!!)
            # [✅ 수정 3] friend_ids가 있으면(=상대방이 있으면) 절대로 여기로 들어오면 안 됨
            calendar_event = None
            if not response_sent_to_db and not recoordination_needed and not friend_ids and schedule_info.get("has_schedule_request"):
                if schedule_info.get("date") and schedule_info.get("time"):
                    calendar_event = await ChatService._add_schedule_to_calendar(user_id, schedule_info, original_text=message)
                    if calendar_event:
                        ai_response = f"✅ 일정이 추가되었습니다: {calendar_event.get('summary')}"

            # 6. 일반 대화 저장
            if not response_sent_to_db and ai_response:
                first_friend_id = friend_ids[0] if friend_ids else None
                await ChatRepository.create_chat_log(
                    user_id=user_id,
                    request_text=None,
                    response_text=ai_response,
                    friend_id=first_friend_id if len(friend_ids) == 1 else None,
                    message_type="ai_response"
                )

            logger.info(f"AI 대화 완료 - 사용자: {user_id}")

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
    async def _get_conversation_history(user_id: str) -> List[Dict[str, str]]:
        """사용자의 최근 대화 히스토리 가져오기"""
        try:
            # 최근 30개의 대화 로그 가져오기 (거절 맥락 포함을 위해 증가)
            recent_logs = await ChatRepository.get_recent_chat_logs(user_id, limit=30)

            conversation_history = []
            for log in recent_logs:
                # 사용자 메시지
                if log.get("request_text"):
                    # 승인/거절 응답인 경우 맥락을 포함한 메시지로 변환
                    if log.get("message_type") == "schedule_approval_response":
                        metadata = log.get("metadata", {})
                        approved = metadata.get("approved", True)
                        proposal = metadata.get("proposal", {})

                        if approved:
                            # 승인한 경우
                            conversation_history.append({
                                "type": "user",
                                "message": f"일정을 승인했습니다: {proposal.get('date', '')} {proposal.get('time', '')}"
                            })
                        else:
                            # 거절한 경우 - 재조율 맥락 포함
                            conversation_history.append({
                                "type": "user",
                                "message": f"일정을 거절했습니다: {proposal.get('date', '')} {proposal.get('time', '')}. 다른 시간으로 재조율을 원합니다."
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
            from src.calendar.calender_service import CalendarService

            # 날짜 파싱
            date_str = schedule_info.get("date", "")
            time_str = schedule_info.get("time", "")
            # [수정] activity를 가져올 때 기본값을 제거하여 None 체크 가능하도록 변경
            activity = schedule_info.get("activity")
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

            # [수정] 일정 제목 생성 로직 개선 (summary가 None이 되지 않도록 처리)
            # activity가 있으면 우선 사용, 없으면 사용자 입력 텍스트(original_text) 사용
            if activity:
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