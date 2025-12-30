from typing import Dict, Any, Optional, List
import logging
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from .a2a_repository import A2ARepository
from .negotiation_engine import NegotiationEngine
from .a2a_protocol import NegotiationStatus
from src.auth.auth_repository import AuthRepository
from src.calendar.calender_service import GoogleCalendarService
from src.auth.auth_service import AuthService
from config.settings import settings
from config.database import supabase
import httpx
import datetime as dt
from datetime import datetime as dt_datetime

from ..chat.chat_repository import ChatRepository
from src.chat.chat_openai_service import OpenAIService
from src.websocket.websocket_manager import manager as ws_manager

logger = logging.getLogger(__name__)

# 한국 시간대
KST = timezone(timedelta(hours=9))

def convert_relative_date(date_str: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """상대 날짜를 YYYY-MM-DD 형식으로 변환"""
    if not date_str:
        return None
    
    if now is None:
        now = datetime.now(KST)
    
    # 이미 YYYY-MM-DD 형식이면 그대로 반환
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str
    
    target_date = None
    
    # 요일 처리 (월요일~일요일)
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    target_weekday = None
    for i, w in enumerate(weekdays):
        if w in date_str:
            target_weekday = i
            break
    
    if target_weekday is not None:
        # 요일 발견
        current_weekday = now.weekday()
        days_ahead = (target_weekday - current_weekday) % 7
        
        # "다음주 화요일" 등 "다음"이 포함된 경우 7일 추가
        if "다음주" in date_str or "다음 주" in date_str:
             days_ahead += 7
        
        target_date = (now + timedelta(days=days_ahead)).date()
        return target_date.strftime("%Y-%m-%d")

    # 상대 날짜 변환
    if "오늘" in date_str:
        target_date = now.date()
    elif "내일" in date_str:
        target_date = (now + timedelta(days=1)).date()
    elif "모레" in date_str:
        target_date = (now + timedelta(days=2)).date()
    elif "다음주" in date_str or "다음 주" in date_str:
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        target_date = (now + timedelta(days=days_until_monday)).date()
    elif "이번주" in date_str or "이번 주" in date_str:
        target_date = now.date()
    else:
        # "12월 12일" 형식
        match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', date_str)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            year = now.year
            if month < now.month or (month == now.month and day < now.day):
                year += 1
            try:
                target_date = datetime(year, month, day).date()
            except ValueError:
                pass
        else:
            # "13일" 형식 (월 없이)
            match_day_only = re.search(r'(\d{1,2})일', date_str)
            if match_day_only:
                day = int(match_day_only.group(1))
                month = now.month
                year = now.year
                if day < now.day:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                try:
                    target_date = datetime(year, month, day).date()
                except ValueError:
                    pass
    
    return target_date.strftime("%Y-%m-%d") if target_date else None


def convert_relative_time(time_str: Optional[str], activity: Optional[str] = None) -> Optional[str]:
    """상대 시간을 HH:MM 형식으로 변환"""
    if not time_str:
        return None
    
    # 이미 HH:MM 형식이면 그대로 반환
    if re.match(r'^\d{1,2}:\d{2}$', time_str):
        return time_str
    
    hour = None
    minute = 0
    
    # 콜론 형식 처리 (예: "5:30", "17:30")
    colon_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
    if colon_match:
        hour = int(colon_match.group(1))
        minute = int(colon_match.group(2))
        
        # 오후/오전 처리
        if "오후" in time_str and hour < 12:
            hour += 12
        elif "오전" in time_str and hour == 12:
            hour = 0
        elif "오전" not in time_str and "오후" not in time_str:
            # 1~6시는 대부분 오후
            if 1 <= hour <= 6:
                hour += 12
        
        return f"{hour:02d}:{minute:02d}"
    
    # "오후 3시", "오전 10시 30분", "5시반" 등
    hour_match = re.search(r'(\d{1,2})\s*시', time_str)
    if hour_match:
        hour = int(hour_match.group(1))
        
        # 오후/오전 처리
        if "오후" in time_str and hour < 12:
            hour += 12
        elif "오전" in time_str and hour == 12:
            hour = 0
        elif "오전" not in time_str and "오후" not in time_str:
            # 1~6시는 대부분 오후
            if 1 <= hour <= 6:
                hour += 12
        
        # "반" 처리 (30분)
        if "반" in time_str:
            minute = 30
        else:
            # 분 처리 (예: "5시 15분", "10시30분")
            min_match = re.search(r'(\d{1,2})\s*분', time_str)
            if min_match:
                minute = int(min_match.group(1))
    
    if hour is not None:
        return f"{hour:02d}:{minute:02d}"
    
    # "점심", "저녁" 등 대략적인 시간
    if "점심" in time_str:
        return "12:00"
    elif "저녁" in time_str:
        return "18:00"
    elif "아침" in time_str:
        return "09:00"
    
    return None

class A2AService:
    
    @staticmethod
    async def start_a2a_session(
        initiator_user_id: str,
        target_user_id: str,
        summary: Optional[str] = None,
        duration_minutes: int = 60,
        use_true_a2a: bool = True,
        origin_chat_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        A2A 세션 시작 및 전체 시뮬레이션 자동 진행
        백엔드에서 모든 단계를 자동으로 처리
        
        Args:
            use_true_a2a: True면 새로운 NegotiationEngine 사용, False면 기존 시뮬레이션 방식
            origin_chat_session_id: 일정 요청을 시작한 원본 채팅방 ID
        """
        try:
            # 1) 세션 생성 (summary는 place_pref에 포함)
            # origin_chat_session_id를 place_pref의 thread_id로 저장하여 추후 활용
            place_pref = {"summary": summary or f"일정 조율"}
            if origin_chat_session_id:
                place_pref["origin_chat_session_id"] = origin_chat_session_id
                place_pref["thread_id"] = origin_chat_session_id # 호환성을 위해 thread_id로도 저장

            session = await A2ARepository.create_session(
                initiator_user_id=initiator_user_id,
                target_user_id=target_user_id,
                intent="schedule",
                place_pref=place_pref if summary or origin_chat_session_id else None,
                participant_user_ids=[initiator_user_id, target_user_id]  # 다중 참여자 지원
            )
            session_id = session["id"]
            
            # 세션 상태를 in_progress로 변경
            await A2ARepository.update_session_status(session_id, "in_progress")
            
            # 2) 사용자 정보 조회 (이름 등)
            initiator = await AuthRepository.find_user_by_id(initiator_user_id)
            target = await AuthRepository.find_user_by_id(target_user_id)
            
            if not initiator or not target:
                raise Exception("사용자 정보를 찾을 수 없습니다.")
            
            initiator_name = initiator.get("name", "사용자")
            target_name = target.get("name", "상대방")
            
            # 3) True A2A 또는 기존 시뮬레이션 실행
            if use_true_a2a:
                # 새로운 NegotiationEngine 사용
                result = await A2AService._execute_true_a2a_negotiation(
                    session_id=session_id,
                    initiator_user_id=initiator_user_id,
                    participant_user_ids=[target_user_id],  # 리스트로 전달
                    summary=summary,
                    duration_minutes=duration_minutes
                )
            else:
                # 기존 시뮬레이션 방식 (하위 호환)
                result = await A2AService._execute_a2a_simulation(
                    session_id=session_id,
                    initiator_user_id=initiator_user_id,
                    target_user_id=target_user_id,
                    initiator_name=initiator_name,
                    target_name=target_name,
                    summary=summary or f"{target_name}와 약속",
                    duration_minutes=duration_minutes
                )
            
            # 4) 승인 필요 시 처리 (실제 승인 요청은 A2A 화면과 Home 알림으로 전달됨)
            # [REMOVED] _send_approval_request_to_chat 호출 - 다중 사용자 흐름에서 사용되지 않는 dead code
            

            # 5) 세션 상태 업데이트
            if result.get("status") == "pending_approval":
                # 승인 대기 중이면 in_progress 유지
                await A2ARepository.update_session_status(session_id, "in_progress")
            elif result.get("status") == "no_slots":
                # 공통 시간 없음 - 재조율 필요
                await A2ARepository.update_session_status(session_id, "in_progress")
            else:
                # 완료
                await A2ARepository.update_session_status(session_id, "completed")
            
            # WebSocket으로 대상자에게 실시간 알림 전송
            try:
                await ws_manager.send_personal_message({
                    "type": "a2a_request",
                    "session_id": session_id,
                    "from_user": initiator_name,
                    "summary": summary or "일정 조율 요청",
                    "proposal": result.get("proposal"),
                    "timestamp": datetime.now(KST).isoformat()
                }, target_user_id)
                logger.info(f"[WS] A2A 알림 전송: {target_user_id}")
            except Exception as ws_err:
                logger.warning(f"[WS] A2A 알림 전송 실패: {ws_err}")
            
            return {
                "status": 200,
                "session_id": session_id,
                "event": result.get("event"),
                "messages": result.get("messages", []),
                "needs_approval": result.get("needs_approval", False),
                "proposal": result.get("proposal")
            }
            
        except Exception as e:
            logger.error(f"A2A 세션 시작 실패: {str(e)}")
            # 실패 시 세션 상태 업데이트
            try:
                await A2ARepository.update_session_status(session_id, "failed")
            except:
                pass
            return {
                "status": 500,
                "error": f"A2A 세션 시작 실패: {str(e)}"
            }
    
    @staticmethod
    async def approve_session(session_id: str, user_id: str) -> Dict[str, Any]:
        """
        A2A 세션의 일정을 승인합니다.
        [수정됨] 다인 세션 지원: 모든 참여자가 승인해야 확정됩니다.
        """
        logger.info(f"🔵 approve_session 시작 - session_id: {session_id}, user_id: {user_id}")
        try:
            from zoneinfo import ZoneInfo
            from datetime import timedelta
            import re
            import json
            
            KST = ZoneInfo("Asia/Seoul")
            
            # 세션 정보 조회
            session = await A2ARepository.get_session(session_id)
            if not session:
                return {"status": 404, "error": "세션을 찾을 수 없습니다."}
            
            target_user_id = session.get("target_user_id")
            initiator_user_id = session.get("initiator_user_id")
            
            # place_pref 파싱
            place_pref = session.get("place_pref", {}) or {}
            if isinstance(place_pref, str):
                try:
                    place_pref = json.loads(place_pref)
                except:
                    place_pref = {}
            
            # [NEW] 전체 참여자 목록 가져오기 (participant_user_ids 우선)
            participant_user_ids = session.get("participant_user_ids") or []
            if not participant_user_ids:
                # Fallback: initiator + target
                participant_user_ids = [initiator_user_id, target_user_id]
            
            # 나간 참여자 제외
            left_participants = set(str(lp) for lp in place_pref.get("left_participants", []))
            active_participants = [str(pid) for pid in participant_user_ids if str(pid) not in left_participants]
            
            logger.info(f"📌 [다인세션] 전체 참여자: {participant_user_ids}, 활성 참여자: {active_participants}")
            
            # [FIX] 다인세션의 경우 thread_id로 모든 세션을 조회하여 승인 상태 동기화
            thread_id = place_pref.get("thread_id")
            all_thread_sessions = [session]
            if thread_id:
                all_thread_sessions = await A2ARepository.get_thread_sessions(thread_id)
                logger.info(f"📌 [다인세션] thread_id={thread_id}, 총 세션 수: {len(all_thread_sessions)}")
            
            # 모든 thread 세션에서 approved_by_list 수집 및 현재 사용자 추가
            approved_by_list = []
            for ts in all_thread_sessions:
                ts_pref = ts.get("place_pref", {})
                if isinstance(ts_pref, str):
                    try: ts_pref = json.loads(ts_pref)
                    except: ts_pref = {}
                for ab in ts_pref.get("approved_by_list", []):
                    if str(ab) not in approved_by_list:
                        approved_by_list.append(str(ab))
            
            # 현재 사용자 추가
            if str(user_id) not in approved_by_list:
                approved_by_list.append(str(user_id))
            
            # 요청자(initiator 또는 rescheduleRequestedBy)는 자동 승인
            reschedule_requester = place_pref.get("rescheduleRequestedBy")
            auto_approved_user = str(reschedule_requester) if reschedule_requester else str(initiator_user_id)
            if auto_approved_user and auto_approved_user not in approved_by_list:
                approved_by_list.append(auto_approved_user)
            
            # 승인 현황 확인
            all_approved = all(str(pid) in approved_by_list for pid in active_participants)
            remaining_count = len([pid for pid in active_participants if str(pid) not in approved_by_list])
            
            logger.info(f"📌 [승인현황] 승인자: {approved_by_list}, 활성참여자: {active_participants}, 전원승인: {all_approved}, 남은수: {remaining_count}")
            
            # [FIX] 모든 thread 세션에 approved_by_list 동기화
            for ts in all_thread_sessions:
                ts_pref = ts.get("place_pref", {})
                if isinstance(ts_pref, str):
                    try: ts_pref = json.loads(ts_pref)
                    except: ts_pref = {}
                ts_pref["approved_by_list"] = approved_by_list
                supabase.table('a2a_session').update({
                    "place_pref": ts_pref,
                    "updated_at": datetime.now().isoformat()
                }).eq('id', ts['id']).execute()
            
            # 아직 모든 사람이 승인하지 않았다면 대기 상태 반환
            if not all_approved:
                user = await AuthRepository.find_user_by_id(user_id)
                user_name = user.get("name", "사용자") if user else "사용자"
                
                # [NEW] 남은 승인자 이름 조회
                pending_user_ids = [pid for pid in active_participants if str(pid) not in approved_by_list]
                pending_names = []
                for pid in pending_user_ids:
                    pending_user = await AuthRepository.find_user_by_id(pid)
                    if pending_user:
                        pending_names.append(pending_user.get("name", "알 수 없음"))
                
                pending_names_str = ", ".join(pending_names) if pending_names else ""
                
                return {
                    "status": 200,
                    "message": f"{user_name}님이 승인했습니다. {remaining_count}명의 승인을 기다리고 있습니다.",
                    "all_approved": False,
                    "approved_count": len(approved_by_list),
                    "total_count": len(active_participants),
                    "remaining_count": remaining_count,
                    "pending_approvers": pending_names  # 프론트엔드가 기대하는 필드명
                }
            
            # ===== 아래부터는 전원 승인 완료 시 실행 =====
            logger.info(f"📌 [다인세션] 전원 승인 완료! 캘린더 등록 진행")
            
            # 승인 권한 확인 (기존 로직 유지하되, 다인세션에서는 참여자면 OK)
            
            # proposal 정보 구성 (여러 소스에서 가져오기)
            details = session.get("details", {}) or {}
            place_pref = session.get("place_pref", {}) or {}
            time_window = session.get("time_window", {}) or {}

            # JSON 파싱 (문자열로 저장된 경우)
            if isinstance(details, str):
                try: details = json.loads(details)
                except: details = {}
            if isinstance(place_pref, str):
                try: place_pref = json.loads(place_pref)
                except: place_pref = {}
            if isinstance(time_window, str):
                try: time_window = json.loads(time_window)
                except: time_window = {}
            
            logger.info(f"세션 정보 확인 - details: {details}, place_pref: {place_pref}, time_window: {time_window}")
            
            # 날짜/시간 정보를 여러 소스에서 찾기
            # 협상 완료 시 place_pref에 proposedDate/proposedTime으로 저장됨
            # 우선순위: place_pref.proposedDate > details > time_window > place_pref.date
            date_str = (place_pref.get("proposedDate") or 
                       details.get("proposedDate") or details.get("proposed_date") or details.get("date") or 
                       time_window.get("date") or place_pref.get("date") or "")
            time_str = (place_pref.get("proposedTime") or 
                       details.get("proposedTime") or details.get("proposed_time") or details.get("time") or 
                       time_window.get("time") or place_pref.get("time") or "")
            location = place_pref.get("location") or details.get("location") or ""
            activity = (place_pref.get("purpose") or details.get("purpose") or 
                       place_pref.get("summary") or place_pref.get("activity") or "약속")
            
            logger.info(f"추출된 정보 - date: {date_str}, time: {time_str}, location: {location}, activity: {activity}")
            
            # 메시지에서 날짜/시간 정보 찾기 (details와 time_window가 비어있을 경우)
            if not date_str or not time_str:
                messages = await A2ARepository.get_session_messages(session_id)
                for msg in reversed(messages):  # 최신 메시지부터
                    msg_content = msg.get("message", {})
                    if isinstance(msg_content, dict):
                        text = msg_content.get("text", "")
                        # 날짜/시간 패턴 추출 (예: "12월 6일 오후 3시", "내일 저녁 7시")
                        if "오후" in text or "오전" in text or "시" in text:
                            # 간단한 패턴 매칭으로 시간 정보 추출
                            if not date_str:
                                date_match = re.search(r'(\d{1,2}월\s*\d{1,2}일|내일|모레|오늘)', text)
                                if date_match:
                                    date_str = date_match.group(1)
                            if not time_str:
                                time_match = re.search(r'(오전|오후|저녁|점심)?\s*\d{1,2}\s*시', text)
                                if time_match:
                                    time_str = time_match.group(0)
                            if date_str and time_str:
                                break
                logger.info(f"메시지에서 추출된 정보 - date: {date_str}, time: {time_str}")
            
            # 시간 파싱
            start_time = None
            end_time = None
            
            if details.get("start_time"):
                start_time = datetime.fromisoformat(details["start_time"].replace("Z", "+00:00")).astimezone(KST)
                end_time = datetime.fromisoformat(details["end_time"].replace("Z", "+00:00")).astimezone(KST)
            elif date_str and time_str:
                # 표준 형식 (YYYY-MM-DD HH:MM 또는 YYYY-MM-DD + HH:MM) 먼저 시도
                try:
                    # time_str이 HH:MM 형식인지 확인
                    if re.match(r'^\d{1,2}:\d{2}$', time_str):
                        # date_str이 YYYY-MM-DD 형식인지 확인
                        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                            combined_iso = f"{date_str}T{time_str}:00"
                            start_time = datetime.fromisoformat(combined_iso).replace(tzinfo=KST)
                            end_time = start_time + timedelta(hours=1)
                            logger.info(f"표준 형식 파싱 성공: {start_time}")
                except Exception as e:
                    logger.warning(f"표준 형식 파싱 실패: {e}")
                
                # 표준 형식 파싱 실패 시 ChatService 사용
                if not start_time:
                    from src.chat.chat_service import ChatService
                    combined = f"{date_str} {time_str}".strip()
                    parsed = await ChatService.parse_time_string(time_str, combined)
                    if parsed:
                        start_time = parsed['start_time']
                        end_time = parsed['end_time']
            
            # 시간 정보가 없으면 기본값 (내일 오후 2시)
            if not start_time:
                start_time = datetime.now(KST).replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(days=1)
                end_time = start_time + timedelta(hours=1)
            
            # 참여자 이름 조회
            initiator = await AuthRepository.find_user_by_id(initiator_user_id)
            target = await AuthRepository.find_user_by_id(target_user_id)
            initiator_name = initiator.get("name", "요청자") if initiator else "요청자"
            target_name = target.get("name", "상대방") if target else "상대방"
            
            # 확정된 정보를 details에 저장 (먼저 상태 업데이트)
            confirmed_details = {
                "proposedDate": start_time.strftime("%m월 %d일"),
                "proposedTime": start_time.strftime("%p %I시").replace("AM", "오전").replace("PM", "오후"),
                "location": location,
                "purpose": activity,
                "proposer": initiator_name,
                "participants": [initiator_name, target_name],
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat()
            }
            
            # 세션 상태를 completed로 업데이트 (모든 thread 세션)
            logger.info(f"🔵 세션 상태 업데이트 시작 - thread의 모든 세션을 completed로")
            for ts in all_thread_sessions:
                await A2ARepository.update_session_status(ts['id'], "completed", confirmed_details)
            logger.info(f"🔵 세션 상태 업데이트 완료 - {len(all_thread_sessions)}개 세션")
            
            # 캘린더 작업을 백그라운드로 실행 (즉시 응답 후 처리)
            async def sync_calendars_background():
                try:
                    from src.calendar.calender_service import CreateEventRequest, GoogleCalendarService
                    
                    # [재조율 시] 기존 캘린더 일정 삭제
                    reschedule_requester = place_pref.get("rescheduleRequestedBy")
                    if reschedule_requester:
                        logger.info(f"🗑️ 재조율 감지 - 기존 캘린더 일정 삭제 시작 (session_id: {session_id})")
                        try:
                            existing_events = supabase.table('calendar_event').select('*').eq('session_id', session_id).execute()
                            
                            if existing_events.data:
                                gc_service = GoogleCalendarService()
                                for old_event in existing_events.data:
                                    owner_id = old_event.get('owner_user_id')
                                    old_google_id = old_event.get('google_event_id')
                                    
                                    if owner_id and old_google_id:
                                        try:
                                            owner_token = await AuthService.get_valid_access_token_by_user_id(owner_id)
                                            if owner_token:
                                                await gc_service.delete_calendar_event(owner_token, old_google_id)
                                                logger.info(f"🗑️ 구글 캘린더 일정 삭제 성공: {old_google_id}")
                                        except Exception as del_error:
                                            logger.warning(f"🗑️ 구글 캘린더 일정 삭제 실패 (무시): {del_error}")
                                
                                supabase.table('calendar_event').delete().eq('session_id', session_id).execute()
                                logger.info(f"🗑️ calendar_event DB 레코드 삭제 완료")
                        except Exception as e:
                            logger.error(f"🗑️ 기존 캘린더 일정 삭제 중 오류: {e}")
                    
                    # [수정됨] 모든 활성 참여자에게 캘린더 일정 추가
                    # active_participants는 외부 스코프에서 정의됨
                    
                    # 참여자 이름 맵 구성
                    participant_names = {}
                    for pid in active_participants:
                        p_user = await AuthRepository.find_user_by_id(pid)
                        participant_names[str(pid)] = p_user.get("name", "사용자") if p_user else "사용자"
                    
                    for pid in active_participants:
                        try:
                            p_name = participant_names.get(str(pid), "사용자")
                            
                            access_token = await AuthService.get_valid_access_token_by_user_id(pid)
                            if not access_token:
                                logger.error(f"유저 {pid} 토큰 갱신 실패")
                                continue
                            
                            # 다른 참여자들 이름 (본인 제외)
                            other_names = [name for uid, name in participant_names.items() if uid != str(pid)]
                            if len(other_names) == 1:
                                evt_summary = f"{other_names[0]}와 {activity}"
                            elif len(other_names) == 2:
                                evt_summary = f"{other_names[0]}, {other_names[1]}와 {activity}"
                            else:
                                evt_summary = f"{other_names[0]} 외 {len(other_names)-1}명과 {activity}"
                            
                            if location:
                                evt_summary += f" ({location})"
                            
                            event_req = CreateEventRequest(
                                summary=evt_summary,
                                start_time=start_time.isoformat(),
                                end_time=end_time.isoformat(),
                                location=location,
                                description="A2A Agent에 의해 자동 생성된 일정입니다.",
                                attendees=[]
                            )
                            
                            gc_service = GoogleCalendarService()
                            evt = await gc_service.create_calendar_event(access_token, event_req)
                            
                            if evt:
                                await A2AService._save_calendar_event_to_db(
                                    session_id=session_id,
                                    owner_user_id=pid,
                                    google_event_id=evt.id,
                                    summary=evt_summary,
                                    location=location,
                                    start_at=start_time.isoformat(),
                                    end_at=end_time.isoformat(),
                                    html_link=evt.htmlLink
                                )
                                logger.info(f"✅ 캘린더 일정 생성 성공: {evt_summary} (user: {pid})")
                                
                        except Exception as e:
                            logger.error(f"유저 {pid} 캘린더 등록 중 에러: {e}")
                    
                    logger.info(f"✅ 백그라운드 캘린더 동기화 완료 (session_id: {session_id})")
                    
                except Exception as e:
                    logger.error(f"❌ 백그라운드 캘린더 동기화 실패: {e}")
            
            # 백그라운드 태스크 시작
            import asyncio
            asyncio.create_task(sync_calendars_background())
            logger.info(f"🚀 캘린더 동기화 백그라운드 태스크 시작 (session_id: {session_id})")
            
            # 즉시 응답 반환
            return {
                "status": 200,
                "message": "일정이 확정되었습니다. 캘린더 동기화 중...",
                "all_approved": True,
                "failed_users": [],
                "confirmed_details": confirmed_details
            }
            
        except Exception as e:
            logger.error(f"세션 승인 실패: {str(e)}", exc_info=True)
            return {"status": 500, "error": str(e)}
    
    @staticmethod
    async def reschedule_session(
        session_id: str,
        user_id: str,
        reason: Optional[str] = None,
        preferred_time: Optional[str] = None,
        manual_input: Optional[str] = None,
        new_date: Optional[str] = None,
        new_time: Optional[str] = None,
        end_date: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        A2A 세션의 재조율을 요청합니다.
        기존 세션을 재활성화하여 협상을 다시 진행합니다.
        """
        try:
            # 세션 정보 조회
            session = await A2ARepository.get_session(session_id)
            if not session:
                return {"status": 404, "error": "세션을 찾을 수 없습니다."}
            
            place_pref = session.get("place_pref", {})
            if isinstance(place_pref, str):
                import json
                try:
                    place_pref = json.loads(place_pref)
                except:
                    place_pref = {}
            
            print(f"🔄 [Reschedule] 기존 세션 재활성화: {session_id}")
            print(f"   - User: {user_id}")
            print(f"   - Reason: {reason}")
            print(f"   - New Date: {new_date}")
            print(f"   - New Time: {new_time}")
            
            # 1. thread_id로 관련된 모든 세션 찾기 (3명 이상 그룹 지원)
            thread_id = place_pref.get("thread_id")
            all_session_ids = [session_id]  # 기본값: 현재 세션만
            
            if thread_id:
                thread_sessions = await A2ARepository.get_thread_sessions(thread_id)
                if thread_sessions:
                    all_session_ids = [s["id"] for s in thread_sessions]
                    print(f"🔗 [Reschedule] thread_id={thread_id}로 {len(all_session_ids)}개 세션 발견")
            
            # 모든 관련 세션 상태를 'in_progress'로 변경
            for sid in all_session_ids:
                await A2ARepository.update_session_status(sid, "in_progress")
            
            # 2. 새로운 제안 시간으로 place_pref 업데이트
            # 새 날짜/시간이 있으면 변환
            target_date = new_date or place_pref.get("proposedDate") or place_pref.get("date")
            target_time = new_time or place_pref.get("proposedTime") or place_pref.get("time")
            
            # 상대 날짜/시간 변환
            formatted_date = convert_relative_date(target_date) or target_date
            formatted_time = convert_relative_time(target_time, place_pref.get("activity")) or target_time
            formatted_end_date = end_date or formatted_date  # 종료 날짜가 없으면 시작 날짜 사용
            formatted_end_time = end_time or (formatted_time if formatted_time else "")  # 종료 시간
            
            # place_pref에 재조율 정보 추가 (시간 범위 포함)
            # [FIX] 재조율 시 기존 승인 목록 및 나간 참여자 초기화
            reschedule_details = {
                "rescheduleReason": reason,
                "rescheduleRequestedBy": user_id,
                "rescheduleRequestedAt": datetime.now().isoformat(),
                "proposedDate": formatted_date,
                "proposedTime": formatted_time,
                "proposedEndDate": formatted_end_date,
                "proposedEndTime": formatted_end_time,
                "approved_by_list": [user_id],  # 재조율 요청자만 승인 상태로 초기화
                "left_participants": [],  # [NEW] 나간 참여자 목록도 초기화 (다시 협상 시작)
            }
            print(f"🔄 [Reschedule] 초기화 - approved_by_list: {[user_id]}, left_participants: []")
            
            # 모든 관련 세션에 재조율 정보 업데이트
            for sid in all_session_ids:
                await A2ARepository.update_session_status(
                    sid, 
                    "in_progress",
                    details=reschedule_details
                )
            
            # 3. 재조율 메시지 추가 (시간 범위 표시)
            initiator_user_id = session.get("initiator_user_id")
            target_user_id = session.get("target_user_id")
            
            time_range_str = f"{formatted_date} {formatted_time} ~ {formatted_end_date} {formatted_end_time}"
            
            reschedule_message = {
                "type": "reschedule_request",
                "title": "재조율 요청",
                "description": f"재조율이 요청되었습니다. 새로운 시간: {time_range_str}",
                "reason": reason,
                "new_date": formatted_date,
                "new_time": formatted_time
            }
            
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=user_id,
                receiver_user_id=target_user_id if user_id == initiator_user_id else initiator_user_id,
                message_type="system",
                message=reschedule_message
            )
            
            # 4. 참여자 정보 수집 (UUID만 사용!)
            # ⚠️ place_pref["participants"]에는 이름이 저장되어 있으므로 사용하지 않음
            # 오직 session["participant_user_ids"]만 사용 (UUID 저장됨)
            participant_user_ids = session.get("participant_user_ids") or []
            
            # participant_user_ids가 비어있으면 target_user_id로 fallback
            if not participant_user_ids:
                participant_user_ids = [target_user_id] if target_user_id else []
            
            # initiator 제외
            participant_user_ids = [uid for uid in participant_user_ids if uid != initiator_user_id]
            
            print(f"🔄 [Reschedule] 협상 재실행 준비:")
            print(f"   - session_id: {session_id}")
            print(f"   - initiator: {initiator_user_id}")
            print(f"   - participants: {participant_user_ids}")
            print(f"   - target_date: {formatted_date}")
            print(f"   - target_time: {formatted_time}")
            
            if not participant_user_ids:
                print(f"⚠️ [Reschedule] 참여자가 없습니다! target_user_id: {target_user_id}")
            
            # 5. 협상 재실행 (백그라운드 태스크로 실행 - 즉시 응답)
            async def run_negotiation_background():
                try:
                    result = await A2AService._execute_true_a2a_negotiation(
                        session_id=session_id,
                        initiator_user_id=initiator_user_id,
                        participant_user_ids=participant_user_ids,
                        summary=place_pref.get("summary") or place_pref.get("activity"),
                        duration_minutes=60,
                        target_date=formatted_date,
                        target_time=formatted_time,
                        location=place_pref.get("location"),
                        all_session_ids=all_session_ids  # 모든 관련 세션에 협상 로그 저장
                    )
                    print(f"✅ [Reschedule Background] 협상 완료: {result.get('status')}")
                except Exception as bg_error:
                    print(f"❌ [Reschedule Background] 협상 실패: {bg_error}")
            
            # 백그라운드에서 협상 실행 (await 없이 즉시 반환)
            asyncio.create_task(run_negotiation_background())
            
            return {
                "status": 200,
                "message": "재조율 요청이 접수되었습니다. AI가 백그라운드에서 협상 중입니다.",
                "session_id": session_id,
                "background_processing": True
            }
            
        except Exception as e:
            logger.error(f"재조율 요청 실패: {str(e)}", exc_info=True)
            return {"status": 500, "error": str(e)}

    @staticmethod
    async def get_available_dates(session_id: str, year: int, month: int) -> Dict[str, Any]:
        """
        특정 월의 모든 참여자 공통 가능 날짜 반환
        """
        try:
            # 세션 및 참여자 확인
            session = await A2ARepository.get_session(session_id)
            if not session:
                return {"status": 404, "error": "세션을 찾을 수 없습니다."}
            
            initiator_user_id = session.get("initiator_user_id")
            target_user_id = session.get("target_user_id")
            participants = [initiator_user_id, target_user_id]
            
            # Google Calendar Service
            service = GoogleCalendarService()
            
            # 시간 범위 설정 (해당 월 1일 ~ 말일)
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            
            tz = timezone(timedelta(hours=9)) # KST
            time_min = datetime(year, month, 1, 0, 0, 0, tzinfo=tz).isoformat()
            time_max = datetime(year, month, last_day, 23, 59, 59, tzinfo=tz).isoformat()
            
            # 모든 참여자의 바쁜 구간 수집
            all_busy_intervals = []
            
            for pid in participants:
                # 토큰 확보
                access_token = await AuthService.get_valid_access_token_by_user_id(pid)
                if not access_token:
                    continue # 토큰 없는 유저는 무시하거나 에러 처리 (여기선 무시하고 진행)
                
                events = await service.get_calendar_events(
                    access_token=access_token,
                    time_min=time_min,
                    time_max=time_max
                )
                
                for e in events:
                    s = e.start.get("dateTime")
                    e_ = e.end.get("dateTime")
                    if s and e_:
                        try:
                            start = datetime.fromisoformat(s.replace("Z", "+00:00"))
                            end = datetime.fromisoformat(e_.replace("Z", "+00:00"))
                            all_busy_intervals.append((start, end))
                        except:
                            continue

            # 병합 및 가용성 체크
            all_busy_intervals.sort(key=lambda x: x[0])
            merged_busy = []
            for s, e in all_busy_intervals:
                if not merged_busy or s > merged_busy[-1][1]:
                    merged_busy.append([s, e])
                else:
                    merged_busy[-1][1] = max(merged_busy[-1][1], e)
            
            # 날짜별 가용 여부 판단
            # 간단한 로직: 하루 중 9시~22시 사이에 1시간 이상 비어있으면 Available로 간주
            
            available_date_strings = []
            
            curr_date = datetime(year, month, 1, tzinfo=tz).date()
            end_date_obj = datetime(year, month, last_day, tzinfo=tz).date()
            
            while curr_date <= end_date_obj:
                # 해당 날짜의 9시 ~ 22시
                day_start = datetime(curr_date.year, curr_date.month, curr_date.day, 9, 0, 0, tzinfo=tz)
                day_end = datetime(curr_date.year, curr_date.month, curr_date.day, 22, 0, 0, tzinfo=tz)
                
                # 해당 날짜에 겹치는 busy interval 찾기
                day_busy = []
                for s, e in merged_busy:
                    # s, e는 aware datetime. 
                    # 겹치는 구간 구하기
                    overlap_start = max(s, day_start)
                    overlap_end = min(e, day_end)
                    
                    if overlap_start < overlap_end:
                        day_busy.append((overlap_start, overlap_end))
                
                # Free time 찾기
                cursor = day_start
                has_slot = False
                for s, e in day_busy:
                    if cursor < s:
                        if (s - cursor).total_seconds() >= 3600: # 1시간 이상
                            has_slot = True
                            break
                    cursor = max(cursor, e)
                
                if not has_slot:
                    if cursor < day_end and (day_end - cursor).total_seconds() >= 3600:
                        has_slot = True
                
                if has_slot:
                    available_date_strings.append(curr_date.strftime("%Y-%m-%d"))
                
                curr_date += timedelta(days=1)

            return {
                "status": 200,
                "available_dates": available_date_strings
            }

        except Exception as e:
            logger.error(f"가용 날짜 조회 실패: {str(e)}")
            return {"status": 500, "error": str(e)}
    
    @staticmethod
    async def _execute_true_a2a_negotiation(
        session_id: str,
        initiator_user_id: str,
        participant_user_ids: List[str],  # ← 다중 참여자 지원
        summary: Optional[str] = None,
        duration_minutes: int = 60,
        target_date: Optional[str] = None,
        target_time: Optional[str] = None,
        location: Optional[str] = None,
        all_session_ids: Optional[List[str]] = None  # 모든 세션에 메시지 저장용
    ) -> Dict[str, Any]:
        """
        True A2A: NegotiationEngine을 사용한 실제 에이전트 간 협상
        각 에이전트가 독립적으로 자신의 캘린더만 접근하며 협상
        
        Args:
            participant_user_ids: 모든 참여자 UUID 리스트 (initiator 제외)
            all_session_ids: 메시지를 저장할 모든 세션 ID 리스트 (다중 세션 지원)
        """
        try:
            from zoneinfo import ZoneInfo
            KST = ZoneInfo("Asia/Seoul")
            
            logger.info(f"True A2A 협상 시작: participants={len(participant_user_ids)}명, date={target_date}, time={target_time}")
            
            # NegotiationEngine 초기화
            engine = NegotiationEngine(
                session_id=session_id,
                initiator_user_id=initiator_user_id,
                participant_user_ids=participant_user_ids,  # 모든 참여자
                activity=summary,
                location=location,
                target_date=target_date,
                target_time=target_time
            )
            
            # 추가 세션 ID 저장 (메시지 동기화용)
            engine.all_session_ids = all_session_ids or [session_id]
            
            messages_log = []
            final_proposal = None
            
            # 협상 실행 (비동기 제너레이터에서 모든 메시지 수집)
            async for message in engine.run_negotiation():
                messages_log.append(message.message)
                if message.proposal:
                    final_proposal = message.proposal.to_dict()
            
            # 협상 결과 확인
            result = engine.get_result()
            
            if result.status == NegotiationStatus.AGREED:
                # 합의 완료 - 캘린더 등록은 approve_session에서 처리
                return {
                    "status": "pending_approval",
                    "messages": messages_log,
                    "needs_approval": True,
                    "proposal": final_proposal
                }
            elif result.status == NegotiationStatus.NEED_HUMAN:
                # 사용자 개입 필요
                return {
                    "status": "need_human",
                    "messages": messages_log,
                    "needs_approval": False,
                    "needs_human_decision": True,
                    "last_proposal": final_proposal,
                    "intervention_reason": result.intervention_reason.value if result.intervention_reason else "unknown"
                }
            else:
                # 협상 실패
                return {
                    "status": "failed",
                    "messages": messages_log,
                    "needs_approval": False
                }
                
        except Exception as e:
            logger.error(f"True A2A 협상 실패: {str(e)}")
            raise e
    
    @staticmethod
    async def _execute_a2a_simulation(
        session_id: str,
        initiator_user_id: str,
        target_user_id: str,
        initiator_name: str,
        target_name: str,
        summary: str,
        duration_minutes: int
    ) -> Dict[str, Any]:
        """에이전트 간 대화 시뮬레이션 실행"""
        
        messages_log = []
        
        openai_service = OpenAIService()

        # 단계 1: 내 캘린더 확인 중
        # [LLM]
        text_msg1 = await openai_service.generate_a2a_message(
            agent_name=f"{initiator_name}의 비서",
            receiver_name=target_name,
            context="내 주인의 캘린더를 확인하려고 함",
            tone="energetic"
        )
        msg1 = {
            "text": text_msg1,
            "step": 1
        }
        await A2ARepository.add_message(
            session_id=session_id,
            sender_user_id=initiator_user_id,
            receiver_user_id=target_user_id,
            message_type="agent_query",
            message=msg1
        )
        messages_log.append(msg1["text"])
        await asyncio.sleep(0.5)
        
        # 단계 2: 상대방 AI와 연결 중
        msg2_connecting = {
            "text": f"{target_name}님의 AI와 연결 중...",
            "step": 2
        }
        await A2ARepository.add_message(
            session_id=session_id,
            sender_user_id=initiator_user_id,
            receiver_user_id=target_user_id,
            message_type="agent_query",
            message=msg2_connecting
        )
        messages_log.append(msg2_connecting["text"])
        await asyncio.sleep(0.5)
        
        # 단계 3: 상대 에이전트가 일정 확인 중
        # [LLM]
        text_msg3 = await openai_service.generate_a2a_message(
            agent_name=f"{target_name}의 비서",
            receiver_name=initiator_name,
            context=f"{initiator_name}의 요청을 받고 일정을 확인하는 중",
            tone="polite"
        )
        msg3_checking = {
            "text": text_msg3,
            "step": 3
        }
        await A2ARepository.add_message(
            session_id=session_id,
            sender_user_id=target_user_id,
            receiver_user_id=initiator_user_id,
            message_type="agent_reply",
            message=msg3_checking
        )
        messages_log.append(msg3_checking["text"])
        await asyncio.sleep(0.5)
        
        # 단계 4: 상대 에이전트가 일정 확인 완료
        # [LLM]
        text_msg4 = await openai_service.generate_a2a_message(
            agent_name=f"{target_name}의 비서",
            receiver_name=initiator_name,
            context="일정 확인을 완료했음",
            tone="confidence"
        )
        msg4_done = {
            "text": text_msg4,
            "step": 4
        }
        await A2ARepository.add_message(
            session_id=session_id,
            sender_user_id=target_user_id,
            receiver_user_id=initiator_user_id,
            message_type="agent_reply",
            message=msg4_done
        )
        messages_log.append(msg4_done["text"])
        await asyncio.sleep(0.5)
        
        # 단계 3: 공통 가용 시간 계산
        try:
            # Google Calendar 토큰 확보
            # initiator 정보 다시 조회
            initiator = await AuthRepository.find_user_by_id(initiator_user_id)
            if not initiator:
                raise Exception("요청자 정보를 찾을 수 없습니다.")
            
            initiator_user_dict = {
                "id": initiator_user_id,
                "email": initiator.get("email")
            }
            me_access = await A2AService._ensure_access_token(initiator_user_dict)
            friend_access = await A2AService._ensure_access_token_by_user_id(target_user_id)
            
            service = GoogleCalendarService()
            now_kst = datetime.now(timezone(timedelta(hours=9)))
            default_min = (now_kst.replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()
            default_max = (now_kst + timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            
            me_events = await service.get_calendar_events(
                access_token=me_access,
                time_min=default_min,
                time_max=default_max,
            )
            friend_events = await service.get_calendar_events(
                access_token=friend_access,
                time_min=default_min,
                time_max=default_max,
            )
            
            # 바쁜 구간 추출
            def to_busy_intervals(events):
                intervals = []
                for e in events:
                    try:
                        s = e.start.get("dateTime")
                        e_ = e.end.get("dateTime")
                        if not s or not e_:
                            continue
                        start = datetime.fromisoformat(s.replace("Z", "+00:00"))
                        end = datetime.fromisoformat(e_.replace("Z", "+00:00"))
                        intervals.append((start, end))
                    except Exception:
                        continue
                return intervals
            
            me_busy = to_busy_intervals(me_events)
            friend_busy = to_busy_intervals(friend_events)
            
            # 병합
            def merge(intervals):
                intervals = sorted(intervals, key=lambda x: x[0])
                merged = []
                for s, e in intervals:
                    if not merged or s > merged[-1][1]:
                        merged.append([s, e])
                    else:
                        merged[-1][1] = max(merged[-1][1], e)
                return [(s, e) for s, e in merged]
            
            all_busy = merge(me_busy + friend_busy)
            
            # Free 구간 계산
            min_boundary = datetime.fromisoformat(default_min.replace("Z", "+00:00"))
            max_boundary = datetime.fromisoformat(default_max.replace("Z", "+00:00"))
            free = []
            cursor = min_boundary
            for s, e in all_busy:
                if e <= min_boundary:
                    continue
                if s >= max_boundary:
                    break
                if s > cursor:
                    free.append((cursor, min(s, max_boundary)))
                cursor = max(cursor, e)
            if cursor < max_boundary:
                free.append((cursor, max_boundary))
            
            # 슬롯 분할
            delta = timedelta(minutes=duration_minutes)
            slots = []
            for s, e in free:
                t = s
                while t + delta <= e:
                    slots.append({
                        "start": t.isoformat(),
                        "end": (t + delta).isoformat(),
                    })
                    t += delta
            
            if not slots:
                # 공통 시간이 없는 경우 - 각자의 차선 시간 제안
                # [LLM]
                text_no_slot = await openai_service.generate_a2a_message(
                    agent_name=f"{initiator_name}의 비서",
                    receiver_name=target_name,
                    context="공통으로 비는 시간이 없어서 난감함",
                    tone="apologetic"
                )
                msg_no_slot = {
                    "text": text_no_slot,
                    "step": 5
                }
                await A2ARepository.add_message(
                    session_id=session_id,
                    sender_user_id=initiator_user_id,
                    receiver_user_id=target_user_id,
                    message_type="agent_query",
                    message=msg_no_slot
                )
                messages_log.append(msg_no_slot["text"])
                await asyncio.sleep(0.5)
                
                # 각자의 가능한 시간 슬롯 찾기
                my_available_slots = []
                friend_available_slots = []
                
                # 내 가능한 시간 슬롯
                for s, e in free:
                    if s >= now_kst:
                        t = s
                        while t + delta <= e:
                            my_available_slots.append({
                                "start": t.isoformat(),
                                "end": (t + delta).isoformat(),
                            })
                            t += delta
                            if len(my_available_slots) >= 3:  # 최대 3개만
                                break
                        if len(my_available_slots) >= 3:
                            break
                
                # 상대방 가능한 시간 슬롯 (간단히 다음 주 시간들로 시뮬레이션)
                next_week = now_kst + timedelta(days=7)
                for i in range(3):
                    slot_time = next_week.replace(hour=14 + i, minute=0, second=0, microsecond=0)
                    friend_available_slots.append({
                        "start": slot_time.isoformat(),
                        "end": (slot_time + delta).isoformat(),
                    })
                
                # 각자의 차선 시간 제안
                if my_available_slots:
                    my_slot = my_available_slots[0]
                    my_slot_dt = datetime.fromisoformat(my_slot["start"].replace("Z", "+00:00"))
                    my_slot_kst = my_slot_dt.astimezone(timezone(timedelta(hours=9)))
                    my_time_str = my_slot_kst.strftime("%Y년 %m월 %d일 %H시 %M분")
                    
                    msg_my_proposal = {
                        "text": f"제가 가능한 시간: {my_time_str}",
                        "step": 5.5
                    }
                    await A2ARepository.add_message(
                        session_id=session_id,
                        sender_user_id=initiator_user_id,
                        receiver_user_id=target_user_id,
                        message_type="proposal",
                        message=msg_my_proposal
                    )
                    messages_log.append(msg_my_proposal["text"])
                    await asyncio.sleep(0.5)
                
                if friend_available_slots:
                    friend_slot = friend_available_slots[0]
                    friend_slot_dt = datetime.fromisoformat(friend_slot["start"].replace("Z", "+00:00"))
                    friend_slot_kst = friend_slot_dt.astimezone(timezone(timedelta(hours=9)))
                    friend_time_str = friend_slot_kst.strftime("%Y년 %m월 %d일 %H시 %M분")
                    
                    msg_friend_proposal = {
                        "text": f"제가 가능한 시간: {friend_time_str}",
                        "step": 5.6
                    }
                    await A2ARepository.add_message(
                        session_id=session_id,
                        sender_user_id=target_user_id,
                        receiver_user_id=initiator_user_id,
                        message_type="proposal",
                        message=msg_friend_proposal
                    )
                    messages_log.append(msg_friend_proposal["text"])
                    await asyncio.sleep(0.5)
                
                # 재조율 요청 메시지
                # [LLM]
                text_reco = await openai_service.generate_a2a_message(
                    agent_name=f"{initiator_name}의 비서",
                    receiver_name=target_name,
                    context="공통 시간이 없어서 각자 가능한 시간을 제안했으니 사용자에게 확인을 요청하겠다고 알림",
                    tone="polite"
                )
                msg_recoordination = {
                    "text": text_reco,
                    "step": 6
                }
                await A2ARepository.add_message(
                    session_id=session_id,
                    sender_user_id=initiator_user_id,
                    receiver_user_id=target_user_id,
                    message_type="system",
                    message=msg_recoordination
                )
                messages_log.append(msg_recoordination["text"])
                
                return {
                    "status": "no_slots",
                    "messages": messages_log,
                    "needs_recoordination": True,
                    "my_available_slots": my_available_slots[:3],
                    "friend_available_slots": friend_available_slots[:3]
                }
            
            # 가장 이른 슬롯 선택
            earliest_slot = slots[0]
            slot_start = earliest_slot["start"]
            slot_end = earliest_slot["end"]
            
            # 시간 포맷팅 (한국 시간)
            start_dt = datetime.fromisoformat(slot_start.replace("Z", "+00:00"))
            start_kst = start_dt.astimezone(timezone(timedelta(hours=9)))
            time_str = start_kst.strftime("%m월 %d일 %H시")
            
            # 단계 5: 공통 시간 제안
            time_str_detail = start_kst.strftime("%Y년 %m월 %d일 %H시 %M분")
            
            # [LLM]
            text_proposal = await openai_service.generate_a2a_message(
                agent_name=f"{initiator_name}의 비서",
                receiver_name=target_name,
                context=f"공통으로 비는 시간을 찾았음: {time_str_detail}",
                tone="happy"
            )
            msg5_proposal = {
                "text": f"{text_proposal} ({time_str_detail})", # 시간 정보는 명확히 덧붙임
                "step": 5,
                "proposed_time": slot_start
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=initiator_user_id,
                receiver_user_id=target_user_id,
                message_type="proposal",
                message=msg5_proposal
            )
            messages_log.append(msg5_proposal["text"])
            await asyncio.sleep(0.5)
            
            # 단계 6: 상대 에이전트가 시간 확인
            # [LLM]
            text_confirm = await openai_service.generate_a2a_message(
                agent_name=f"{target_name}의 비서",
                receiver_name=initiator_name,
                context=f"{time_str_detail}에 만나는 것으로 확인하고 동의함",
                tone="polite"
            )
            msg6_confirm = {
                "text": text_confirm,
                "step": 6
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=target_user_id,
                receiver_user_id=initiator_user_id,
                message_type="confirm",
                message=msg6_confirm
            )
            messages_log.append(msg6_confirm["text"])
            await asyncio.sleep(0.5)
            
            # 단계 7: 사용자 승인 대기 (가등록 전)
            msg7_waiting = {
                "text": "사용자 승인을 기다리는 중...", # 시스템 메시지는 그대로 유지하거나 간단히 변경
                "step": 7
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=initiator_user_id,
                receiver_user_id=target_user_id,
                message_type="system",
                message=msg7_waiting
            )
            messages_log.append(msg7_waiting["text"])
            
            # 승인 필요 플래그 설정 - 일정은 아직 생성하지 않음
            # 모든 참여자가 승인한 후에만 handle_schedule_approval에서 캘린더에 일정 추가
            return {
                "status": "pending_approval",
                "messages": messages_log,
                "needs_approval": True,
                "proposal": {
                    "date": start_kst.strftime("%Y년 %m월 %d일"),
                    "time": start_kst.strftime("%H시 %M분"),
                    "start_time": slot_start,
                    "end_time": slot_end,
                    "participants": [initiator_name, target_name]
                }
            }
            
        except Exception as e:
            logger.error(f"A2A 시뮬레이션 실행 실패: {str(e)}")
            raise e
    
    @staticmethod
    async def _ensure_access_token(current_user: dict) -> str:
        """Google Calendar 액세스 토큰 확보 (만료 시 리프레시)"""
        db_user = await AuthRepository.find_user_by_email(current_user["email"])
        if not db_user:
            raise Exception("사용자 정보를 찾을 수 없습니다.")

        access_token = db_user.get("access_token")
        refresh_token = db_user.get("refresh_token")
        expiry = db_user.get("token_expiry") or db_user.get("expiry")

        def _to_dt(x):
            if not x:
                return None
            if isinstance(x, dt.datetime):
                return x
            try:
                return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
            except Exception:
                return None

        expiry_dt = _to_dt(expiry)
        needs_refresh = False
        if access_token and expiry_dt:
            now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
            needs_refresh = (expiry_dt - now_utc).total_seconds() < 60
        elif access_token and not expiry_dt:
            needs_refresh = False
        else:
            needs_refresh = True

        if not needs_refresh and access_token:
            return access_token

        if not refresh_token:
            raise Exception("Google 재로그인이 필요합니다 (refresh_token 없음).")

        async with httpx.AsyncClient(timeout=15) as client:
            data = {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
            r = await client.post("https://oauth2.googleapis.com/token", data=data)
            if r.status_code != 200:
                raise Exception(f"Google 토큰 갱신 실패: {r.text}")
            tok = r.json()

        new_access = tok["access_token"]
        now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        new_expiry = (now_utc + dt.timedelta(seconds=tok.get("expires_in", 3600))).isoformat()

        try:
            await AuthRepository.update_google_user_info(
                email=current_user["email"],
                access_token=new_access,
                refresh_token=refresh_token,
                profile_image=None,
                name=None,
                token_expiry=new_expiry,
            )
        except TypeError:
            await AuthRepository.update_google_user_info(
                email=current_user["email"],
                access_token=new_access,
                refresh_token=refresh_token,
                profile_image=None,
                name=None,
            )
        return new_access
    
    @staticmethod
    async def _ensure_access_token_by_user_id(user_id: str) -> str:
        """사용자 ID로 Google Calendar 액세스 토큰 확보"""
        db_user = await AuthRepository.find_user_by_id(user_id)
        if not db_user:
            raise Exception("대상 사용자를 찾을 수 없습니다.")

        access_token = db_user.get("access_token")
        refresh_token = db_user.get("refresh_token")
        expiry = db_user.get("token_expiry") or db_user.get("expiry")

        def _to_dt(x):
            if not x:
                return None
            if isinstance(x, dt.datetime):
                return x
            try:
                return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
            except Exception:
                return None

        expiry_dt = _to_dt(expiry)
        needs_refresh = False
        if access_token and expiry_dt:
            now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
            needs_refresh = (expiry_dt - now_utc).total_seconds() < 60
        elif access_token and not expiry_dt:
            needs_refresh = False
        else:
            needs_refresh = True

        if not needs_refresh and access_token:
            return access_token

        if not refresh_token:
            raise Exception("대상 사용자의 Google 재로그인이 필요합니다 (refresh_token 없음).")

        async with httpx.AsyncClient(timeout=15) as client:
            data = {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
            r = await client.post("https://oauth2.googleapis.com/token", data=data)
            if r.status_code != 200:
                raise Exception(f"Google 토큰 갱신 실패: {r.text}")
            tok = r.json()

        new_access = tok["access_token"]
        now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        new_expiry = (now_utc + dt.timedelta(seconds=tok.get("expires_in", 3600))).isoformat()

        try:
            await AuthRepository.update_google_user_info(
                email=db_user.get("email"),
                access_token=new_access,
                refresh_token=refresh_token,
                profile_image=None,
                name=None,
                token_expiry=new_expiry,
            )
        except TypeError:
            await AuthRepository.update_google_user_info(
                email=db_user.get("email"),
                access_token=new_access,
                refresh_token=refresh_token,
                profile_image=None,
                name=None,
            )
        return new_access
    
    @staticmethod
    async def _save_calendar_event_to_db(
        session_id: str,
        owner_user_id: str,
        google_event_id: str,
        summary: str,
        location: Optional[str],
        start_at: str,
        end_at: str,
        html_link: Optional[str] = None
    ) -> Optional[str]:
        """calendar_event 테이블에 이벤트 저장"""
        try:
            # start_at, end_at을 datetime으로 변환
            def parse_datetime(s: str) -> datetime:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                if "T" in s and "+" not in s and "Z" not in s:
                    s += "+09:00"
                return datetime.fromisoformat(s)
            
            start_dt = parse_datetime(start_at)
            end_dt = parse_datetime(end_at)
            
            # 이미 존재하는지 확인
            existing = supabase.table('calendar_event').select('id').eq(
                'google_event_id', google_event_id
            ).execute()
            
            if existing.data and len(existing.data) > 0:
                # 이미 존재하면 업데이트
                event_id = existing.data[0]['id']
                supabase.table('calendar_event').update({
                    "session_id": session_id,
                    "summary": summary,
                    "location": location,
                    "start_at": start_dt.isoformat(),
                    "end_at": end_dt.isoformat(),
                    "html_link": html_link,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq('id', event_id).execute()
                return event_id
            else:
                # 새로 생성
                event_data = {
                    "session_id": session_id,
                    "owner_user_id": owner_user_id,
                    "google_event_id": google_event_id,
                    "summary": summary,
                    "location": location,
                    "start_at": start_dt.isoformat(),
                    "end_at": end_dt.isoformat(),
                    "html_link": html_link,
                    "time_zone": "Asia/Seoul",
                    "status": "confirmed"
                }
                response = supabase.table('calendar_event').insert(event_data).execute()
                if response.data and len(response.data) > 0:
                    return response.data[0]['id']
            return None
        except Exception as e:
            logger.error(f"calendar_event 저장 실패: {str(e)}")
            return None
    
    @staticmethod
    async def start_multi_user_session(
        initiator_user_id: str,
        target_user_ids: List[str],
        summary: str,
        date: Optional[str] = None,
        time: Optional[str] = None,
        location: Optional[str] = None,
        activity: Optional[str] = None,
        duration_minutes: int = 60,
        force_new: bool = False,
        use_true_a2a: bool = True,
        origin_chat_session_id: Optional[str] = None  # 원본 채팅 세션 ID 추가
    ) -> Dict[str, Any]:
        """
        다중 사용자 일정 조율 세션 시작
        - force_new: True이면 기존 세션을 재사용하지 않고 무조건 새로 생성
        - use_true_a2a: True이면 NegotiationEngine 사용, False이면 기존 시뮬레이션
        여러 참여자와 동시에 일정을 조율합니다.
        기존 세션이 있으면 재사용합니다.
        """
        try:
            # 0) 기존 세션 확인 (같은 참여자들로 진행 중이거나 최근 세션)
            # 각 target에 대해 기존 세션 찾기
            existing_session_map = {}  # target_id -> session
            all_existing_sessions = []
            
            for target_id in target_user_ids:
                existing_session = await A2ARepository.find_existing_session(
                    initiator_user_id=initiator_user_id,
                    target_user_ids=[target_id]  # 1:1 세션 기준으로 찾기
                )
                if existing_session:
                    # [✅ 수정] 완료된 세션은 재사용하지 않고 새로운 세션 생성
                    if existing_session.get("status") == "completed":
                        logger.info(f"완료된 세션 발견 (ID: {existing_session['id']}) - 재사용하지 않음")
                        continue
                        
                    existing_session_map[target_id] = existing_session
                    all_existing_sessions.append(existing_session)
            
            # 기존 세션이 하나라도 있고, 진행 중이거나 최근에 생성된 경우 재사용
            # [✅ 수정] force_new가 True이면 재사용하지 않음
            reuse_existing = False
            if not force_new:
                reuse_existing = len(existing_session_map) > 0
            
            if reuse_existing:
                # 기존 세션들에서 thread_id 추출
                thread_id = None
                for session in all_existing_sessions:
                    place_pref = session.get("place_pref")
                    if isinstance(place_pref, dict) and place_pref.get("thread_id"):
                        thread_id = place_pref.get("thread_id")
                        break
                
                # thread_id가 없으면 새로 생성
                if not thread_id:
                    thread = await A2ARepository.create_thread(
                        initiator_id=initiator_user_id,
                        participant_ids=target_user_ids,
                        title=summary
                    )
                    thread_id = thread["id"]
                
                logger.info(f"기존 세션 재사용: thread_id={thread_id}, 기존 세션 수={len(existing_session_map)}")
                
                # 기존 세션의 참여자 정보 가져오기
                sessions = []
                initiator = await AuthRepository.find_user_by_id(initiator_user_id)
                initiator_name = initiator.get("name", "사용자") if initiator else "사용자"
                
                for target_id in target_user_ids:
                    target_user = await AuthRepository.find_user_by_id(target_id)
                    target_name = target_user.get("name", "사용자") if target_user else "사용자"
                    
                    # 기존 세션이 있으면 재사용
                    if target_id in existing_session_map:
                        existing = existing_session_map[target_id]
                        session_id = existing["id"]
                        
                        # 세션이 completed 상태면 in_progress로 변경
                        if existing.get("status") == "completed":
                            await A2ARepository.update_session_status(session_id, "in_progress")
                        
                        # place_pref 업데이트 (새로운 정보 반영)
                        place_pref = existing.get("place_pref", {})
                        if isinstance(place_pref, dict):
                            place_pref.update({
                                "thread_id": thread_id,
                                "participants": target_user_ids,
                                "location": location,  # [FIX] 기존 세션 location 재사용 안 함
                                "activity": activity or place_pref.get("activity"),
                                "date": date or place_pref.get("date"),
                                "time": time or place_pref.get("time"),
                                "purpose": activity or place_pref.get("activity")  # [FIX] purpose 업데이트
                            })
                            # place_pref 업데이트는 Supabase에서 직접 업데이트 필요
                            # 일단 세션은 재사용
                        
                        sessions.append({
                            "session_id": session_id,
                            "target_id": target_id,
                            "target_name": target_name
                        })
                    else:
                        # 기존 세션이 없으면 새로 생성 (같은 thread_id 사용)
                        # 요청 시간을 YYYY-MM-DD HH:MM 형식으로 변환
                        formatted_requested_date = convert_relative_date(date) or date
                        formatted_requested_time = convert_relative_time(time, activity) or time
                        
                        place_pref = {
                            "summary": summary,
                            "thread_id": thread_id,
                            "participants": target_user_ids,
                            "location": location,
                            "activity": activity,
                            "date": date,
                            "time": time,
                            # 원래 요청 시간 (YYYY-MM-DD HH:MM 형식으로 변환하여 저장)
                            "requestedDate": formatted_requested_date,
                            "requestedTime": formatted_requested_time,
                            "purpose": activity,
                            # 원본 채팅 세션 ID 저장 (거절 시 이 채팅방에 알림 전송)
                            "origin_chat_session_id": origin_chat_session_id
                        }
                        session = await A2ARepository.create_session(
                            initiator_user_id=initiator_user_id,
                            target_user_id=target_id,
                            intent="schedule",
                            place_pref=place_pref,
                            time_window={"date": date, "time": time, "duration_minutes": duration_minutes} if date or time else None,
                            participant_user_ids=[initiator_user_id] + target_user_ids  # 다중 참여자 지원
                        )
                        sessions.append({
                            "session_id": session["id"],
                            "target_id": target_id,
                            "target_name": target_name
                        })
                        await A2ARepository.update_session_status(session["id"], "in_progress")
            else:
                # 기존 세션이 없으면 새로 생성
                # 1) Thread 생성 (그룹 세션)
                participant_names = []
                for target_id in target_user_ids:
                    target_user = await AuthRepository.find_user_by_id(target_id)
                    if target_user:
                        participant_names.append(target_user.get("name", "사용자"))
                
                thread = await A2ARepository.create_thread(
                    initiator_id=initiator_user_id,
                    participant_ids=target_user_ids,
                    title=summary
                )
                thread_id = thread["id"]
                
                # 2) 각 참여자마다 세션 생성 (같은 thread_id로 연결)
                sessions = []
                initiator = await AuthRepository.find_user_by_id(initiator_user_id)
                initiator_name = initiator.get("name", "사용자") if initiator else "사용자"
                
                for target_id in target_user_ids:
                    target_user = await AuthRepository.find_user_by_id(target_id)
                    target_name = target_user.get("name", "사용자") if target_user else "사용자"
                    
                    # 세션 생성 (place_pref에 thread_id와 모든 참여자 정보 저장)
                    # 요청 시간을 YYYY-MM-DD HH:MM 형식으로 변환
                    formatted_requested_date = convert_relative_date(date) or date
                    formatted_requested_time = convert_relative_time(time, activity) or time
                    
                    place_pref = {
                        "summary": summary,
                        "thread_id": thread_id,
                        "participants": target_user_ids,
                        "location": location,
                        "activity": activity,
                        "date": date,
                        "time": time,
                        # 원래 요청 시간 (YYYY-MM-DD HH:MM 형식으로 변환하여 저장)
                        "requestedDate": formatted_requested_date,
                        "requestedTime": formatted_requested_time,
                        "purpose": activity,  # [FIX] purpose 추가
                        # 원본 채팅 세션 ID 저장 (거절 시 이 채팅방에 알림 전송)
                        "origin_chat_session_id": origin_chat_session_id
                    }
                    
                    session = await A2ARepository.create_session(
                        initiator_user_id=initiator_user_id,
                        target_user_id=target_id,
                        intent="schedule",
                        place_pref=place_pref,
                        time_window={"date": date, "time": time, "duration_minutes": duration_minutes} if date or time else None,
                        participant_user_ids=[initiator_user_id] + target_user_ids  # 다중 참여자 지원
                    )
                    sessions.append({
                        "session_id": session["id"],
                        "target_id": target_id,
                        "target_name": target_name
                    })
                    
                    # 세션 상태를 in_progress로 변경
                    await A2ARepository.update_session_status(session["id"], "in_progress")
            
            # 3) 다중 사용자 일정 조율 시뮬레이션 실행
            # 기존 세션을 재사용하는 경우, 기존 메시지에 이어서 추가
            
            # [FIX] 기존 세션에서 location 재사용 안 함 - 현재 요청의 location만 사용
            final_location = location

            # True A2A 또는 기존 시뮬레이션 실행
            if use_true_a2a:
                # NegotiationEngine 사용 - 모든 참여자에게 협상
                first_session = sessions[0] if sessions else None
                if first_session:
                    # 모든 세션 ID 수집 (메시지 동기화용)
                    all_session_ids = [s["session_id"] for s in sessions]
                    
                    result = await A2AService._execute_true_a2a_negotiation(
                        session_id=first_session["session_id"],
                        initiator_user_id=initiator_user_id,
                        participant_user_ids=target_user_ids,  # 모든 참여자
                        summary=summary,
                        duration_minutes=duration_minutes,
                        target_date=date,
                        target_time=time,
                        location=final_location,
                        all_session_ids=all_session_ids  # 모든 세션에 메시지 저장
                    )
                else:
                    result = {"status": "failed", "messages": [], "needs_approval": False}
            else:
                # 기존 시뮬레이션 방식
                result = await A2AService._execute_multi_user_coordination(
                    thread_id=thread_id,
                    sessions=sessions,
                    initiator_user_id=initiator_user_id,
                    initiator_name=initiator_name,
                    date=date,
                    time=time,
                    location=final_location,
                    activity=activity,
                    duration_minutes=duration_minutes,
                    reuse_existing=reuse_existing
                )
            
            # 4) 모든 세션 완료 처리 (기존 세션 재사용 시에도 상태 업데이트)
            for session_info in sessions:
                # completed 상태로 변경하지 않고, in_progress 유지 (대화가 계속될 수 있음)
                # 필요시에만 completed로 변경
                pass
            
            # WebSocket으로 모든 대상자에게 실시간 알림 전송
            try:
                for target_id in target_user_ids:
                    await ws_manager.send_personal_message({
                        "type": "a2a_request",
                        "thread_id": thread_id,
                        "from_user": initiator_name,
                        "summary": summary or "일정 조율 요청",
                        "proposal": result.get("proposal"),
                        "timestamp": datetime.now(KST).isoformat()
                    }, target_id)
                logger.info(f"[WS] 다중 A2A 알림 전송: {target_user_ids}")
            except Exception as ws_err:
                logger.warning(f"[WS] 다중 A2A 알림 전송 실패: {ws_err}")
            
            return {
                "status": 200,
                "thread_id": thread_id,
                "session_ids": [s["session_id"] for s in sessions],
                "event": result.get("event"),
                "messages": result.get("messages", []),
                "needs_approval": result.get("needs_approval", False),
                "proposal": result.get("proposal")
            }
            
        except Exception as e:
            logger.error(f"다중 사용자 세션 시작 실패: {str(e)}", exc_info=True)
            return {
                "status": 500,
                "error": f"다중 사용자 세션 시작 실패: {str(e)}"
            }
    
    @staticmethod
    async def _execute_multi_user_coordination(
        thread_id: str,
        sessions: List[Dict[str, Any]],
        initiator_user_id: str,
        initiator_name: str,
        date: Optional[str],
        time: Optional[str],
        location: Optional[str],
        activity: Optional[str],
        duration_minutes: int,
        reuse_existing: bool = False
    ) -> Dict[str, Any]:
        """
        다중 사용자 일정 조율 시뮬레이션 실행
        각 참여자의 Agent가 캘린더를 확인하고 일정을 조율합니다.
        """
        messages = []
        openai_service = OpenAIService()
        
        try:
            # 기존 세션 재사용 시, 기존 메시지가 있으면 건너뛰고 새 요청만 추가
            if not reuse_existing:
                # 1) 초기 메시지: 요청자 Agent가 모든 참여자에게 알림 (새 세션인 경우만)
                # [LLM]
                text_request = await openai_service.generate_a2a_message(
                    agent_name=f"{initiator_name}의 비서",
                    receiver_name="모두",
                    context=f"{initiator_name}님이 {date or '일정'} {time or ''}에 약속을 요청함 (활동: {activity or '없음'})",
                    tone="energetic"
                )
                
                for session_info in sessions:
                    await A2ARepository.add_message(
                        session_id=session_info["session_id"],
                        sender_user_id=initiator_user_id,
                        receiver_user_id=session_info["target_id"],
                        message_type="agent_query",
                        message={"text": text_request}
                    )
                    messages.append({
                        "session_id": session_info["session_id"],
                        "sender": f"{initiator_name}봇",
                        "text": text_request
                    })
            else:
                # 기존 세션 재사용 시, 새로운 요청 메시지만 추가
                request_text = f"새로운 일정 요청: {date or '일정'} {time or ''}"
                if activity:
                    request_text += f" 활동: {activity}"
                
                for session_info in sessions:
                    await A2ARepository.add_message(
                        session_id=session_info["session_id"],
                        sender_user_id=initiator_user_id,
                        receiver_user_id=session_info["target_id"],
                        message_type="agent_query",
                        message={"text": request_text}
                    )
                    messages.append({
                        "session_id": session_info["session_id"],
                        "sender": f"{initiator_name}봇",
                        "text": request_text
                    })
            
            # 2) 요청자 포함 모든 참여자의 Agent가 자신의 캘린더 확인
            availability_results = []
            
            # 먼저 요청자의 일정 확인
            # [LLM]
            text_init_check = await openai_service.generate_a2a_message(
                agent_name=f"{initiator_name}의 비서",
                receiver_name="모두",
                context=f"먼저 {initiator_name}님의 일정을 확인해보겠다고 알림",
                tone="polite"
            )
            for session_info in sessions:
                await A2ARepository.add_message(
                    session_id=session_info["session_id"],
                    sender_user_id=initiator_user_id,
                    receiver_user_id=session_info["target_id"],
                    message_type="agent_query",
                    message={"text": text_init_check, "step": 1}
                )
            messages.append({
                "sender": f"{initiator_name}봇",
                "text": text_init_check
            })
            
            # 요청자 캘린더 확인
            initiator_availability = await A2AService._check_user_availability(
                user_id=initiator_user_id,
                date=date,
                time=time,
                duration_minutes=duration_minutes
            )
            
            availability_results.append({
                "user_id": initiator_user_id,
                "user_name": initiator_name,
                "session_id": sessions[0]["session_id"] if sessions else None,
                "available": initiator_availability["available"],
                "conflict_events": initiator_availability.get("conflict_events", []),
                "available_slots": initiator_availability.get("available_slots", [])
            })
            
            # 각 참여자의 Agent가 자신의 캘린더 확인
            for session_info in sessions:
                target_id = session_info["target_id"]
                target_name = session_info["target_name"]
                
                # "사용자의 일정을 확인 중입니다..." 메시지
                # [LLM]
                text_target_check = await openai_service.generate_a2a_message(
                    agent_name=f"{target_name}의 비서",
                    receiver_name=initiator_name,
                    context=f"{target_name}님의 일정을 확인해보겠다고 알림",
                    tone="polite"
                )
                await A2ARepository.add_message(
                    session_id=session_info["session_id"],
                    sender_user_id=target_id,
                    receiver_user_id=initiator_user_id,
                    message_type="agent_query",
                    message={"text": text_target_check, "step": 2}
                )
                messages.append({
                    "session_id": session_info["session_id"],
                    "sender": f"{target_name}봇",
                    "text": text_target_check
                })
                
                # 캘린더 확인
                availability = await A2AService._check_user_availability(
                    user_id=target_id,
                    date=date,
                    time=time,
                    duration_minutes=duration_minutes
                )
                
                availability_results.append({
                    "user_id": target_id,
                    "user_name": target_name,
                    "session_id": session_info["session_id"],
                    "available": availability["available"],
                    "conflict_events": availability.get("conflict_events", []),
                    "available_slots": availability.get("available_slots", [])
                })
            
            # 3) 시간이 지정된 경우: 모든 참여자(요청자 포함) 가능 여부 확인
            if date and time:
                all_available = all(r.get("available", False) and not r.get("error") for r in availability_results)
                
                if all_available:
                    # 모든 참여자(요청자 포함)가 가능하면 확정 제안
                    # 공통 시간 확인 완료 메시지
                    # [LLM]
                    text_common = await openai_service.generate_a2a_message(
                        agent_name=f"{initiator_name}의 비서",
                        receiver_name="모두",
                        context=f"모든 참여자의 일정을 확인했고 {date} {time}에 모두 가능하다고 알림",
                        tone="happy"
                    )
                    for session_info in sessions:
                        await A2ARepository.add_message(
                            session_id=session_info["session_id"],
                            sender_user_id=initiator_user_id,
                            receiver_user_id=session_info["target_id"],
                            message_type="agent_reply",
                            message={"text": text_common, "step": 3}
                        )
                    
                    # 참여자 목록 (요청자 포함)
                    all_participant_names = [r["user_name"] for r in availability_results]
                    proposal_data = {
                        "date": date,
                        "time": time,
                        "location": location or None,
                        "activity": activity,
                        "participants": all_participant_names,
                        "proposedDate": date,  # 프론트엔드용
                        "proposedTime": time,  # 프론트엔드용
                        "start_time": None,  # 시간 파싱 필요
                        "end_time": None
                    }
                    
                    # 시간 파싱 (proposal에 start_time, end_time 추가)
                    try:
                        from src.chat.chat_service import ChatService
                        from zoneinfo import ZoneInfo
                        from datetime import timedelta
                        import re
                        KST = ZoneInfo("Asia/Seoul")
                        today = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
                        
                        # 날짜 파싱
                        parsed_date = None
                        date_str = date.strip() if date else ""
                        
                        if "오늘" in date_str:
                            parsed_date = today
                        elif "내일" in date_str:
                            parsed_date = today + timedelta(days=1)
                        elif "모레" in date_str:
                            parsed_date = today + timedelta(days=2)
                        elif "다음주" in date_str or "이번주" in date_str:
                            weekday_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
                            for day_name, day_num in weekday_map.items():
                                if day_name in date_str:
                                    days_ahead = day_num - today.weekday()
                                    if "다음주" in date_str:
                                        days_ahead += 7 if days_ahead > 0 else 14
                                    else:
                                        if days_ahead < 0:
                                            days_ahead += 7
                                    parsed_date = today + timedelta(days=days_ahead)
                                    break
                        else:
                            # "화요일", "수요일" 등 요일만 있는 경우
                            weekday_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
                            for day_name, day_num in weekday_map.items():
                                if day_name in date_str:
                                    days_ahead = day_num - today.weekday()
                                    if days_ahead <= 0:  # 오늘이거나 이미 지난 요일이면 다음 주
                                        days_ahead += 7
                                    parsed_date = today + timedelta(days=days_ahead)
                                    logger.info(f"📅 요일 파싱: '{date_str}' -> {parsed_date.strftime('%Y-%m-%d')}, 오늘 요일: {today.weekday()}, 목표 요일: {day_num}")
                                    break
                        
                        if not parsed_date:
                            parsed_date = today + timedelta(days=1)  # 기본값: 내일
                        
                        # 시간 파싱
                        time_str = time.strip() if time else ""
                        hour = 14  # 기본값: 오후 2시
                        
                        if "점심" in time_str:
                            hour = 12
                        elif "저녁" in time_str or "밤" in time_str:
                            hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                            if hour_match:
                                hour = int(hour_match.group(1))
                                if hour < 12:
                                    hour += 12  # 저녁/밤이면 PM으로 처리
                            else:
                                hour = 19  # 저녁 기본값
                        elif "오전" in time_str:
                            hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                            if hour_match:
                                hour = int(hour_match.group(1))
                        elif "오후" in time_str:
                            hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                            if hour_match:
                                hour = int(hour_match.group(1))
                                if hour < 12:
                                    hour += 12
                        else:
                            hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                            if hour_match:
                                hour = int(hour_match.group(1))
                        
                        # 최종 datetime 생성
                        start_time = parsed_date.replace(hour=hour, minute=0)
                        end_time = start_time + timedelta(hours=1)  # 기본 1시간
                        
                        proposal_data["start_time"] = start_time.isoformat()
                        proposal_data["end_time"] = end_time.isoformat()
                        # 파싱된 정확한 날짜/시간으로 업데이트
                        proposal_data["proposedDate"] = start_time.strftime("%-m월 %-d일")
                        am_pm = "오전" if start_time.hour < 12 else "오후"
                        display_hour = start_time.hour if start_time.hour <= 12 else start_time.hour - 12
                        if display_hour == 0:
                            display_hour = 12
                        proposal_data["proposedTime"] = f"{am_pm} {display_hour}시"
                        proposal_data["date"] = start_time.strftime("%Y년 %-m월 %-d일")
                        
                        logger.info(f"📅 Proposal 날짜 파싱: '{date}' '{time}' -> {proposal_data['proposedDate']} {proposal_data['proposedTime']}")
                    except Exception as e:
                        logger.warning(f"시간 파싱 실패: {str(e)}")
                    
                    # 모든 참여자(요청자 포함)에게 승인 요청 메시지 전송
                    all_participant_ids = [r["user_id"] for r in availability_results]
                    for participant_id in all_participant_ids:
                        # 요청자 본인에게는 "조율이 완료되었습니다" 같은 멘트 (선택 사항)
                        # 여기서는 상대방(수신자)에게 안내하는 것이 목적이므로 구분

                        noti_message = ""
                        if participant_id != initiator_user_id:
                            # 상대방에게: "OO님이 ~로 재조율을 요청했습니다."
                            # [FIX] 문구 수정: "재조율" -> "제안" (상황에 따라 다르게 할 수도 있지만 일단 중립적으로)
                            # 그리고 중복 전송 방지 로직 추가
                            
                            # 1. 문구 수정
                            action_text = "일정 재조율을 요청했습니다" if reuse_existing else "일정을 제안했습니다"
                            noti_message = f"🔔 {initiator_name}님이 {date} {time}으로 {action_text}."

                            # 2. 중복 방지: 최근 메시지 확인
                            from src.chat.chat_repository import ChatRepository
                            recent_logs = await ChatRepository.get_recent_chat_logs(participant_id, limit=1)
                            is_duplicate = False
                            if recent_logs:
                                last_msg = recent_logs[0]
                                # 마지막 메시지가 AI 응답이고, 내용이 동일하면 중복으로 간주
                                if last_msg.get('response_text') == noti_message:
                                    is_duplicate = True
                            
                            if not is_duplicate:
                                await ChatRepository.create_chat_log(
                                    user_id=participant_id,
                                    request_text=None,
                                    response_text=noti_message,
                                    friend_id=None,
                                    message_type="ai_response" # 일반 텍스트 메시지
                                )
                            else:
                                logger.info(f"중복된 알림 메시지라 전송 생략: {participant_id} -> {noti_message}")

                    # [REMOVED] 승인 요청 카드 전송 - dead code (A2A 화면과 Home 알림으로 대체됨)
                    

                    return {
                        "messages": messages,
                        "needs_approval": True,
                        "proposal": proposal_data
                    }
                else:
                    # [수정됨] 일부 불가능하면 재조율 필요
                    from src.chat.chat_repository import ChatRepository # Chat 화면 알림용 import

                    unavailable_results = [r for r in availability_results if not r["available"]]

                    # 각 불가능한 참여자가 직접 거절 메시지를 보내도록 수정
                    for r in unavailable_results:
                        target_id = r["user_id"]
                        target_name = r["user_name"]
                        conflicts = r.get("conflict_events", [])

                        # 내 자신(initiator)이 안 되는 경우
                        if target_id == initiator_user_id:
                            # [LLM]
                            text_reject_me = await openai_service.generate_a2a_message(
                                agent_name=f"{initiator_name}의 비서",
                                receiver_name="모두",
                                context=f"내 주인({initiator_name})에게 해당 시간에 {len(conflicts)}개의 일정이 있어 불가능하다고 알림",
                                tone="apologetic"
                            )
                            # A2A 메시지 (내 비서가 나에게/상대에게 알림)
                            for session_info in sessions:
                                await A2ARepository.add_message(
                                    session_id=session_info["session_id"],
                                    sender_user_id=initiator_user_id,
                                    receiver_user_id=session_info["target_id"],
                                    message_type="agent_reply",
                                    message={"text": text_reject_me, "step": 3}
                                )
                        else:
                            # 상대방(target)이 안 되는 경우 -> 상대방 봇이 말해야 함
                            # [LLM]
                            text_reject_target = await openai_service.generate_a2a_message(
                                agent_name=f"{target_name}의 비서",
                                receiver_name=initiator_name,
                                context=f"{target_name}님이 해당 시간에 일정이 있어 불가능하다고 알림 ({len(conflicts)}개 충돌)",
                                tone="apologetic"
                            )
                            
                            # [LLM]
                            text_reco_target = await openai_service.generate_a2a_message(
                                agent_name=f"{target_name}의 비서",
                                receiver_name=initiator_name,
                                context="다른 시간을 제안해주시면 다시 조율하겠다고 정중히 요청",
                                tone="polite"
                            )

                            # 1. 상대방 봇 -> 나(initiator)에게 거절 메시지 전송
                            # 해당 상대방과의 세션 찾기
                            target_session = next((s for s in sessions if s["target_id"] == target_id), None)
                            if target_session:
                                # 거절 사유
                                await A2ARepository.add_message(
                                    session_id=target_session["session_id"],
                                    sender_user_id=target_id,     # [수정] 보내는 사람: 상대방
                                    receiver_user_id=initiator_user_id, # 받는 사람: 나
                                    message_type="agent_reply",
                                    message={"text": text_reject_target, "step": 3}
                                )
                                messages.append({
                                    "session_id": target_session["session_id"],
                                    "sender": f"{target_name}봇",
                                    "text": text_reject_target
                                })

                                # 재조율 요청 멘트
                                await A2ARepository.add_message(
                                    session_id=target_session["session_id"],
                                    sender_user_id=target_id,     # [수정] 보내는 사람: 상대방
                                    receiver_user_id=initiator_user_id,
                                    message_type="proposal", # proposal 타입으로 변경하여 강조
                                    message={"text": text_reco_target, "step": 4}
                                )

                    # [추가] 메인 Chat 화면에 "재조율 필요" 알림 보내기
                    # 충돌난 사람들 이름 모으기
                    unavailable_names = [r["user_name"] for r in unavailable_results]
                    main_chat_msg = f"❌ 일정 충돌 감지\n{', '.join(unavailable_names)}님의 일정 문제로 {date} {time} 약속 진행이 어렵습니다.\n다른 시간을 입력해주세요."

                    await ChatRepository.create_chat_log(
                        user_id=initiator_user_id,
                        request_text=None,
                        response_text=main_chat_msg,
                        message_type="system", # 시스템 알림 처리
                        metadata={
                            "needs_recoordination": True,
                            "unavailable_users": unavailable_names
                        }
                    )

                    # 충돌 감지 시 세션 상태를 needs_recoordination으로 변경하여 pending-requests에서 제외
                    for session_info in sessions:
                        await A2ARepository.update_session_status(
                            session_id=session_info["session_id"],
                            status="needs_recoordination"
                        )
                    logger.info(f"🔄 일정 충돌 감지 - 세션 상태를 needs_recoordination으로 변경")

                    return {
                        "status": 200, # 이게 있어야 chat_service가 정상 종료로 인식함
                        "messages": messages,
                        "needs_approval": False,
                        "needs_recoordination": True, # 재조율 플래그
                        "unavailable_users": [r["user_name"] for r in unavailable_results],
                        "conflict_details": {r["user_name"]: r.get("conflict_events", []) for r in unavailable_results}
                    }
            else:
                # 시간이 지정되지 않은 경우: 가능한 시간 후보 제안
                # 각 참여자가 가능한 시간 슬롯 제안
                all_slots = []
                for result in availability_results:
                    if result.get("available_slots"):
                        slots_text = f"{result['user_name']}님은 "
                        slots_text += ", ".join([f"{s['date']} {s['time']}" for s in result["available_slots"][:3]])
                        slots_text += " 가능합니다."
                        
                        await A2ARepository.add_message(
                            session_id=result["session_id"],
                            sender_user_id=result["user_id"],
                            receiver_user_id=initiator_user_id,
                            message_type="proposal",
                            message={"text": slots_text}
                        )
                        all_slots.extend(result["available_slots"])
                
                # 공통 가능 시간 찾기 (간단한 로직)
                # 실제로는 더 정교한 알고리즘이 필요하지만, 일단 첫 번째 제안된 시간 사용
                if all_slots:
                    common_slot = all_slots[0]
                    proposal_msg = f"{common_slot['date']} {common_slot['time']}로 사용자에게 일정확인 바랍니다."
                    
                    for session_info in sessions:
                        await A2ARepository.add_message(
                            session_id=session_info["session_id"],
                            sender_user_id=initiator_user_id,
                            receiver_user_id=session_info["target_id"],
                            message_type="proposal",
                            message={"text": proposal_msg}
                        )
                    
                    return {
                        "messages": messages,
                        "needs_approval": True,
                        "proposal": {
                            "date": common_slot.get("date"),
                            "time": common_slot.get("time"),
                            "location": location,
                            "participants": [r["user_name"] for r in availability_results]
                        }
                    }
            
            return {
                "messages": messages,
                "needs_approval": False
            }
            
        except Exception as e:
            logger.error(f"다중 사용자 조율 실행 실패: {str(e)}", exc_info=True)
            return {
                "messages": messages,
                "needs_approval": False,
                "error": str(e)
            }
    
    @staticmethod
    async def _check_user_availability(
        user_id: str,
        date: Optional[str],
        time: Optional[str],
        duration_minutes: int
    ) -> Dict[str, Any]:
        """
        사용자의 특정 시간 가능 여부 확인
        """
        try:
            # 사용자 정보 조회
            user = await AuthRepository.find_user_by_id(user_id)
            if not user:
                return {"available": False, "error": "사용자를 찾을 수 없습니다."}
            
            # Google Calendar 액세스 토큰 확인
            access_token = await A2AService._ensure_access_token_by_user_id(user_id)
            if not access_token:
                return {"available": True, "note": "캘린더 연동 없음, 가능한 것으로 간주"}
            
            # 날짜/시간 파싱
            if not date or not time:
                # 시간이 지정되지 않으면 Google Calendar에서 실제 가용 시간 슬롯 조회
                try:
                    from zoneinfo import ZoneInfo
                    KST = ZoneInfo("Asia/Seoul")
                    # 내일 날짜부터 3일간 조회
                    base_date = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    end_check_date = base_date + timedelta(days=3)
                    
                    # 캘린더 이벤트 가져오기
                    gc_service = GoogleCalendarService()
                    events = await gc_service.get_calendar_events(
                        access_token=access_token,
                        time_min=base_date,
                        time_max=end_check_date
                    )
                    
                    # Busy 구간 정리
                    busy_intervals = []
                    for e in events:
                        start_str = e.start.get("dateTime")
                        end_str = e.end.get("dateTime")
                        if start_str and end_str:
                            s_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            e_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                            busy_intervals.append((s_dt, e_dt))
                            
                    busy_intervals.sort(key=lambda x: x[0])
                    
                    # 가용 슬롯 찾기 (09:00 ~ 22:00 사이, 1시간 단위)
                    available_slots = []
                    
                    curr_check = base_date
                    while curr_check < end_check_date and len(available_slots) < 3:
                        # 하루의 시작/끝 (09시 ~ 22시)
                        day_start = curr_check.replace(hour=9, minute=0, second=0)
                        day_end = curr_check.replace(hour=22, minute=0, second=0)
                        
                        # 이 날의 busy 구간 필터링
                        day_busy = []
                        for s, e in busy_intervals:
                            # 겹치는 구간만 추출
                            # s가 day_end보다 전이고, e가 day_start보다 후면 겹침
                            if s < day_end and e > day_start:
                                day_busy.append((max(s, day_start), min(e, day_end)))
                        
                        # 빈 시간 찾기
                        cursor = day_start
                        while cursor < day_end and len(available_slots) < 3:
                            slot_end = cursor + timedelta(hours=1)
                            
                            # cursor ~ slot_end 구간이 day_busy와 겹치는지 확인
                            is_busy = False
                            for s, e in day_busy:
                                if cursor < e and slot_end > s:
                                    is_busy = True
                                    # 겹치면 busy 끝나는 시간으로 점프 (최적화)
                                    if e > cursor:
                                        cursor = e
                                    break
                            
                            if not is_busy:
                                # 찾음
                                date_str = cursor.strftime("%m월 %d일")
                                time_str = cursor.strftime("%p %I시").replace("AM", "오전").replace("PM", "오후")
                                available_slots.append({"date": date_str, "time": time_str})
                                cursor += timedelta(hours=1) # 다음 슬롯
                            else:
                                if is_busy:
                                     # 이미 위에서 jump 했거나, 1시간 더함 (단순화: 30분 단위 이동 등 가능하지만 여기선 1시간)
                                     # 위 jump 로직이 완전하지 않을 수 있으므로 안전하게 30분 단위 이동
                                     pass
                                     
                            # cursor 갱신 (loop 안전장치)
                            # is_busy 였으면 cursor는 busy end로 이동했을 수도 있음.
                            # 만약 이동 안했으면 30분 추가
                            if is_busy:
                                # cursor가 그대로라면 강제 전진
                                cursor += timedelta(minutes=30)
                        
                        curr_check += timedelta(days=1)
                    
                    if not available_slots:
                         # 정말 꽉 찼으면 기본값
                         available_slots = [{"date": "가능한 시간 없음", "time": ""}]

                    return {
                        "available": False, # 특정 시간이 없으므로 False가 맞으나, 로직상 제안을 위해 True로 보내거나 client 처리?
                        # 원본 로직 유지: 시간이 지정되지 않으면 available=True로 보내고 slots를 줌
                        "available": True,
                        "available_slots": available_slots
                    }

                except Exception as e:
                    logger.error(f"가용 시간 조회 실패: {e}")
                    # 실패 시 빈 리스트
                    return {"available": True, "available_slots": []}
            
            # 날짜/시간 파싱 (ChatService의 파싱 로직 활용)
            from src.chat.chat_service import ChatService
            from datetime import timedelta
            from zoneinfo import ZoneInfo
            
            KST = ZoneInfo("Asia/Seoul")
            today = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
            
            # 날짜 파싱
            parsed_date = None
            date_str = date.strip()
            if "오늘" in date_str:
                parsed_date = today
            elif "내일" in date_str:
                parsed_date = today + timedelta(days=1)
            elif "모레" in date_str:
                parsed_date = today + timedelta(days=2)
            elif "다음주" in date_str or "이번주" in date_str:
                # 요일 파싱 (예: "금요일")
                weekday_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
                for day_name, day_num in weekday_map.items():
                    if day_name in date_str:
                        days_ahead = day_num - today.weekday()
                        if "다음주" in date_str:
                            # 다음주는 반드시 7일 이상 추가
                            if days_ahead <= 0:
                                days_ahead += 7
                            else:
                                days_ahead += 7  # 다음주이면 무조건 7일 추가
                        else:
                            # 이번주
                            if days_ahead < 0:
                                days_ahead += 7
                        parsed_date = today + timedelta(days=days_ahead)
                        logger.info(f"📅 날짜 파싱: '{date_str}' -> {parsed_date.strftime('%Y-%m-%d')}, 오늘 요일: {today.weekday()}, 목표 요일: {day_num}, days_ahead: {days_ahead}")
                        break
                if not parsed_date:
                    parsed_date = today + timedelta(days=7)
            else:
                # 숫자로 된 날짜 파싱 시도
                match = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", date_str)
                if match:
                    month = int(match.group(1))
                    day = int(match.group(2))
                    current_year = today.year
                    parsed_date = datetime(current_year, month, day, tzinfo=KST)
                else:
                    # 기본값: 내일
                    parsed_date = today + timedelta(days=1)
            
            # 시간 파싱
            parsed_time = None
            time_str = time.strip()
            
            # "오후 2시", "저녁 7시", "점심" 등 파싱
            if "점심" in time_str:
                parsed_time = parsed_date.replace(hour=12, minute=0)
            elif "저녁" in time_str or "밤" in time_str:
                hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                if hour_match:
                    hour = int(hour_match.group(1))
                    parsed_time = parsed_date.replace(hour=hour, minute=0)
                else:
                    parsed_time = parsed_date.replace(hour=19, minute=0)  # 기본 저녁 7시
            elif "오전" in time_str:
                hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                if hour_match:
                    hour = int(hour_match.group(1))
                    parsed_time = parsed_date.replace(hour=hour, minute=0)
            elif "오후" in time_str:
                hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                if hour_match:
                    hour = int(hour_match.group(1)) + 12
                    parsed_time = parsed_date.replace(hour=hour, minute=0)
            else:
                # 숫자만 있는 경우
                hour_match = re.search(r"(\d{1,2})\s*시", time_str)
                if hour_match:
                    hour = int(hour_match.group(1))
                    # 12시 이후면 오후로 간주
                    if hour < 12:
                        hour += 12
                    parsed_time = parsed_date.replace(hour=hour, minute=0)
            
            if not parsed_time:
                # 기본값: 오후 2시
                parsed_time = parsed_date.replace(hour=14, minute=0)
            
            # 종료 시간 계산
            end_time = parsed_time + timedelta(minutes=duration_minutes)
            
            # Google Calendar API로 해당 시간대 이벤트 조회
            google_calendar = GoogleCalendarService()
            try:
                # 시간 범위 설정 (시작 1시간 전 ~ 종료 1시간 후)
                time_min = (parsed_time - timedelta(hours=1)).isoformat()
                time_max = (end_time + timedelta(hours=1)).isoformat()
                
                events = await google_calendar.get_calendar_events(
                    access_token=access_token,
                    calendar_id="primary",
                    time_min=time_min,
                    time_max=time_max
                )
                
                # 충돌 확인
                conflict_events = []
                for event in events:
                    # CalendarEvent 모델: start와 end는 dict 타입
                    event_start_dict = event.start if isinstance(event.start, dict) else {}
                    event_end_dict = event.end if isinstance(event.end, dict) else {}
                    
                    event_start = event_start_dict.get("dateTime") or event_start_dict.get("date")
                    event_end = event_end_dict.get("dateTime") or event_end_dict.get("date")
                    
                    if event_start and event_end:
                        # datetime 파싱
                        try:
                            if "T" in event_start:
                                event_start_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
                            else:
                                event_start_dt = datetime.fromisoformat(event_start + "T00:00:00+09:00")
                            
                            if "T" in event_end:
                                event_end_dt = datetime.fromisoformat(event_end.replace("Z", "+00:00"))
                            else:
                                event_end_dt = datetime.fromisoformat(event_end + "T00:00:00+09:00")
                            
                            # 시간대 변환
                            if event_start_dt.tzinfo is None:
                                event_start_dt = event_start_dt.replace(tzinfo=KST)
                            if event_end_dt.tzinfo is None:
                                event_end_dt = event_end_dt.replace(tzinfo=KST)
                            
                            # 충돌 확인: 요청 시간과 기존 일정이 겹치는지
                            # 겹치는 조건: (parsed_time < event_end_dt) and (end_time > event_start_dt)
                            logger.debug(f"🔍 충돌 확인: 요청={parsed_time.isoformat()} ~ {end_time.isoformat()}, 이벤트({event.summary})={event_start_dt.isoformat()} ~ {event_end_dt.isoformat()}")
                            if parsed_time < event_end_dt and end_time > event_start_dt:
                                logger.info(f"❌ 충돌 발견: {event.summary} ({event_start_dt.isoformat()} ~ {event_end_dt.isoformat()})")
                                conflict_events.append({
                                    "summary": event.summary,
                                    "start": event_start_dt.isoformat(),
                                    "end": event_end_dt.isoformat()
                                })
                        except Exception as e:
                            logger.warning(f"이벤트 시간 파싱 실패: {event_start}, {event_end}, 오류: {str(e)}")
                            continue
                
                if conflict_events:
                    logger.info(f"사용자 {user_id}의 {parsed_time} 시간에 {len(conflict_events)}개의 충돌 일정 발견")
                    return {
                        "available": False,
                        "conflict_events": conflict_events,
                        "requested_time": parsed_time.isoformat()
                    }
                else:
                    logger.info(f"사용자 {user_id}의 {parsed_time} 시간에 일정 없음 - 가능")
                    return {
                        "available": True,
                        "conflict_events": []
                    }
                    
            except Exception as e:
                logger.error(f"Google Calendar 이벤트 조회 실패: {str(e)}")
                # 오류 발생 시 안전하게 불가능한 것으로 처리
                return {
                    "available": False,
                    "error": f"캘린더 확인 실패: {str(e)}"
                }
            
        except Exception as e:
            logger.error(f"사용자 가능 여부 확인 실패: {str(e)}")
            return {"available": True, "error": str(e)}
    

    @staticmethod
    async def handle_schedule_approval(
        thread_id: str,
        session_ids: List[str],
        user_id: str,
        approved: bool,
        proposal: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        일정 승인/거절 처리 (로직 보강)
        1. 승인 상태 확인 방식을 '리스트 신뢰'에서 '개별 로그 전수 조사'로 변경하여 동기화 오류 방지
        2. 캘린더 등록 실패 시(상대방 토큰 만료 등) 에러를 무시하지 않고 결과 메시지에 포함
        """
        try:
            print(f"📌 [handle_schedule_approval] Started - approved={approved}, user_id={user_id}")
            print(f"📌 [handle_schedule_approval] Proposal: {proposal}")
            # 1. 세션 및 참여자 정보 확보
            sessions = []
            if thread_id:
                sessions = await A2ARepository.get_thread_sessions(thread_id)
            elif session_ids:
                for sid in session_ids:
                    sess = await A2ARepository.get_session(sid)
                    if sess:
                        sessions.append(sess)
            
            if not sessions:
                return {"status": 404, "error": "세션을 찾을 수 없습니다."}

            # 모든 참여자 ID 추출 (중복 제거)
            all_participants = set()
            left_participants_set = set()  # 나간 참여자들
            
            for session in sessions:
                # place_pref에서 left_participants 추출
                place_pref = session.get("place_pref", {})
                if isinstance(place_pref, str):
                    import json
                    try:
                        place_pref = json.loads(place_pref)
                    except:
                        place_pref = {}
                
                for lp in place_pref.get("left_participants", []):
                    left_participants_set.add(str(lp))
                
                # participant_user_ids 우선 사용 (다중 참여자 지원)
                participant_ids = session.get("participant_user_ids") or []
                logger.info(f"📌 [DEBUG] 세션 {session.get('id')} - participant_user_ids: {participant_ids}")
                logger.info(f"📌 [DEBUG] 세션 {session.get('id')} - initiator: {session.get('initiator_user_id')}, target: {session.get('target_user_id')}")
                
                if participant_ids:
                    for pid in participant_ids:
                        if pid:
                            all_participants.add(str(pid))
                else:
                    # Fallback: initiator + target
                    if session.get("initiator_user_id"): 
                        all_participants.add(str(session.get("initiator_user_id")))
                    if session.get("target_user_id"): 
                        all_participants.add(str(session.get("target_user_id")))
            
            # 나간 참여자 제외
            active_participants = all_participants - left_participants_set
            logger.info(f"📌 전체 참여자({len(all_participants)}): {all_participants}")
            logger.info(f"📌 나간 참여자({len(left_participants_set)}): {left_participants_set}")
            logger.info(f"📌 활성 참여자({len(active_participants)}): {active_participants}")
            
            user = await AuthRepository.find_user_by_id(user_id)
            user_name = user.get("name", "사용자") if user else "사용자"

            # [중요] 활성 참여자가 1명뿐인 경우 즉시 완료 처리
            if len(active_participants) < 2:
                logger.warning(f"활성 참여자가 1명뿐입니다. 즉시 승인될 수 있습니다. Active: {active_participants}")

            if approved:
                # 2. [수정됨] 승인 현황 재계산 (Source of Truth: 개별 유저의 최신 로그)
                
                real_approved_users = set()
                
                # 현재 요청한 유저는 승인한 것으로 간주
                real_approved_users.add(str(user_id))
                
                # [FIX] 원래 요청자(initiator)는 본인이 요청한 것이므로 자동 승인 처리
                # 재조율의 경우 rescheduleRequestedBy가 요청자
                for session in sessions:
                    place_pref = session.get("place_pref", {})
                    if isinstance(place_pref, str):
                        try:
                            import json
                            place_pref = json.loads(place_pref)
                        except Exception as e:
                            logger.error(f"place_pref JSON 파싱 오류: {str(e)}")
                            place_pref = {}
                    
                    # 재조율 요청자가 있으면 그 사람이 요청자 (자동 승인)
                    req_by = place_pref.get("rescheduleRequestedBy")
                    if req_by:
                        req_by_str = str(req_by)
                        real_approved_users.add(req_by_str)
                        logger.info(f"📌 재조율 요청자 자동 승인: {req_by_str}")
                    else:
                        # 재조율이 아니면 원래 initiator가 요청자 (자동 승인)
                        initiator_id = session.get("initiator_user_id")
                        if initiator_id:
                            real_approved_users.add(str(initiator_id))
                            logger.info(f"📌 원래 요청자(initiator) 자동 승인: {initiator_id}")
            
                # 다른 활성 참여자들의 승인 상태 확인 (나간 사람 제외)
                for pid in active_participants:
                    pid_str = str(pid)
                    if pid_str == str(user_id): continue 
                    if pid_str in real_approved_users: continue 

                    # 해당 유저의 가장 최근 'schedule_approval' 로그 조회
                    query = supabase.table('chat_log').select('*').eq(
                        'user_id', pid_str
                    ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1)
                    
                    res = query.execute()
                    if res.data:
                        log_meta = res.data[0].get('metadata', {})
                        if str(log_meta.get('approved_by')) == pid_str:
                            real_approved_users.add(pid_str)
            
                # 전원 승인 여부 판단 (활성 참여자 기준)
                all_approved = len(real_approved_users) >= len(active_participants)
                approved_list = list(real_approved_users)

                logger.info(f"승인 현황: {len(real_approved_users)}/{len(active_participants)} - {real_approved_users}")

                # 3. 메타데이터 동기화 (활성 참여자만)
                for participant_id in active_participants:
                    pid_str = str(participant_id)
                    # 각 참여자의 로그 찾기
                    log_query = supabase.table('chat_log').select('*').eq(
                        'user_id', pid_str
                    ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1).execute()
                    
                    if log_query.data:
                        target_log = log_query.data[0]
                        meta = target_log.get('metadata', {})
                        
                        # 업데이트할 메타데이터 구성
                        # approved_by 필드는 "그 유저가 승인했는지"를 나타내므로, 
                        # 현재 participant_id가 이번 요청자(user_id)라면 user_id로 업데이트, 아니면 기존 값 유지
                        new_approved_by = str(user_id) if pid_str == str(user_id) else meta.get('approved_by')
                        
                        new_meta = {
                            **meta,
                            "approved_by_list": approved_list, # 최신 리스트 전파
                            "approved_by": new_approved_by
                        }
                        
                        # 업데이트 실행
                        supabase.table('chat_log').update({
                            "metadata": new_meta
                        }).eq("id", target_log['id']).execute()
                
                # 4. 결과 반환 (UI에서 사용)
                # 만약 방금 업데이트한 로그가 내 로그라면, 그 메타데이터를 반환값에 포함
                # 그러나 편의상 위에서 만든 new_meta(내꺼 기준)를 반환
                
                # 내 로그 찾기
                my_log_query = supabase.table('chat_log').select('*').eq(
                    'user_id', str(user_id)
                ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1).execute()
                
                final_meta = {}
                if my_log_query.data:
                     final_meta = my_log_query.data[0].get('metadata', {})

                if all_approved:
                    # 4. 승인 완료 처리 (캘린더 등록 등)
                    # ... (기존 로직 유지) ...
                    
                    # 캘린더 등록 로직 (생략 - 아래 있는 기존 코드 실행됨)
                    pass

                # 승인 알림 메시지 (채팅방)
                approval_msg_text = f"{user_name}님이 일정을 승인했습니다."
                if all_approved:
                    approval_msg_text += " (전원 승인 완료 - 캘린더 등록 중...)"
                else:
                    remaining = len(active_participants) - len(real_approved_users)
                    approval_msg_text += f" (남은 승인: {remaining}명)"

                for session in sessions:
                    await A2ARepository.add_message(
                        session_id=session["id"],
                        sender_user_id=user_id,
                        receiver_user_id=session.get("target_user_id") if session.get("target_user_id") != user_id else session.get("initiator_user_id"),
                        message_type="confirm",
                        message={"text": approval_msg_text, "step": 8 if all_approved else 7.5}
                    )

                # 4. [수정됨] 전원 승인 시 캘린더 추가 및 예외 처리 강화
                failed_users = [] # 실패한 유저 이름/ID 저장
                
                if all_approved:
                    # 시간 파싱 (기존 로직 활용)
                    from zoneinfo import ZoneInfo
                    from src.chat.chat_service import ChatService
                    KST = ZoneInfo("Asia/Seoul")
                    
                    start_time = None
                    end_time = None

                    if proposal.get("start_time"):
                         start_time = datetime.fromisoformat(proposal["start_time"].replace("Z", "+00:00")).astimezone(KST)
                         end_time = datetime.fromisoformat(proposal["end_time"].replace("Z", "+00:00")).astimezone(KST)
                    else:
                        parsed = await ChatService.parse_time_string(proposal.get("time"), f"{proposal.get('date')} {proposal.get('time')}")
                        if parsed:
                            start_time = parsed['start_time']
                            end_time = parsed['end_time']
                    
                    if not start_time:
                         start_time = datetime.now(KST) + timedelta(days=1) # Fallback

                    # 활성 참여자에게만 캘린더 이벤트 등록
                    for pid in active_participants:
                        p_name = "알 수 없음"
                        try:
                            # 유저 이름 조회 (에러 메시지용)
                            p_user = await AuthRepository.find_user_by_id(pid)
                            p_name = p_user.get("name", "사용자") if p_user else "사용자"

                            # 토큰 확보
                            access_token = await AuthService.get_valid_access_token_by_user_id(pid)
                            if not access_token:
                                logger.error(f"유저 {pid} 토큰 갱신 실패. 캘린더 등록 불가.")
                                failed_users.append(p_name)
                                continue
                            
                            from src.calendar.calender_service import CreateEventRequest, GoogleCalendarService
                            
                            # 제목 설정
                            # 1. 제안된 활동 내용 가져오기
                            act = proposal.get("activity")

                            # 2. 상대방 이름 찾기 (나를 제외한 참여자)
                            # user_name은 현재 루프의 pid에 해당하는 유저 이름 (즉, 캘린더 주인)
                            # 따라서 캘린더 주인이 아닌 다른 사람들의 이름을 모아야 함
                            other_participants = [p for p in proposal.get("participants", []) if p != p_name] # p_name은 위에서 조회한 p_user.name

                            # 만약 이름을 못 찾았다면(리스트가 비었다면) 전체 참여자 중 본인 제외 시도
                            if not other_participants:
                                # proposal['participants']가 정확하지 않을 경우를 대비해
                                # 상대방 이름(target_name 등)을 추론하거나 단순하게 처리
                                others_str = "상대방"
                            else:
                                others_str = ", ".join(other_participants)

                            # 3. 제목 조합: "상대방과 활동내용"
                            if act:
                                evt_summary = f"{others_str}와 {act}"
                            else:
                                evt_summary = f"{others_str}와 약속"

                                # 장소가 있다면 뒤에 붙임
                            if proposal.get("location"):
                                evt_summary += f" ({proposal.get('location')})"

                            # attendees=[] 로 설정하여 중복 초대 메일 방지하고 각자 캘린더에 생성
                            event_req = CreateEventRequest(
                                summary=evt_summary,
                                start_time=start_time.isoformat(),
                                end_time=end_time.isoformat(),
                                location=proposal.get("location"),
                                description="A2A Agent에 의해 자동 생성된 일정입니다.",
                                attendees=[] 
                            )
                            
                            gc_service = GoogleCalendarService()
                            evt = await gc_service.create_calendar_event(access_token, event_req)
                            
                            if evt:
                                # DB 저장
                                await A2AService._save_calendar_event_to_db(
                                    session_id=sessions[0]["id"],
                                    owner_user_id=pid,
                                    google_event_id=evt.id,
                                    summary=evt_summary,
                                    location=proposal.get("location"),
                                    start_at=start_time.isoformat(),
                                    end_at=end_time.isoformat(),
                                    html_link=evt.htmlLink
                                )
                            else:
                                failed_users.append(p_name)
                                
                        except Exception as e:
                            logger.error(f"유저 {pid} 캘린더 등록 중 에러: {e}")
                            failed_users.append(p_name)

                    # 결과 메시지 구성
                    if not failed_users:
                        final_msg_text = "모든 참여자의 캘린더에 일정이 정상 등록되었습니다."
                    else:
                        final_msg_text = f"일정이 확정되었으나, 다음 사용자의 캘린더 등록에 실패했습니다: {', '.join(failed_users)}. (권한/로그인 확인 필요)"

                    final_msg = { "text": final_msg_text, "step": 9 }
                    
                    for session in sessions:
                        await A2ARepository.add_message(
                            session_id=session["id"],
                            sender_user_id=user_id,
                            receiver_user_id=session.get("target_user_id") if session.get("target_user_id") != user_id else session.get("initiator_user_id"),
                            message_type="final",
                            message=final_msg
                        )

                    from src.chat.chat_repository import ChatRepository

                    for pid in active_participants:
                        await ChatRepository.create_chat_log(
                            user_id=pid,
                            request_text=None,
                            response_text=final_msg_text, # "모든 참여자의 캘린더에..."
                            friend_id=None,
                            message_type="ai_response" # 일반 텍스트 메시지로 저장
                        )

                    # 세션 상태를 completed로 업데이트
                    for session in sessions:
                        await A2ARepository.update_session_status(session["id"], "completed")
                    logger.info(f"✅ 세션 상태 completed로 업데이트 완료")

                    return {
                        "status": 200,
                        "message": final_msg_text,
                        "all_approved": True,
                        "failed_users": failed_users
                    }

                return {
                    "status": 200,
                    "message": "승인이 처리되었습니다.",
                    "all_approved": False,
                    "approved_by_list": approved_list
                }

            else:
                print(f"📌 [handle_schedule_approval] Entered ELSE branch (approved=False)")
                print(f"📌 [handle_schedule_approval] sessions count: {len(sessions)}")
                # [New] 재조율 요청인 경우 (reason 또는 preferred_time이 존재함)
                if proposal.get("reason") or proposal.get("preferred_time"):
                    print(f"📌 [handle_schedule_approval] Reschedule condition MET - reason={proposal.get('reason')}")
                    logger.info(f"재조율 요청 감지 - user_id: {user_id}")
                    
                    # 기존 세션을 '완료됨' 처리하지 않고 업데이트 (User Request)
                    # "재협상 요청을 하면 새로운 세션이 시작되는게 아니라, 기존 약속이 변경되는걸 원해"
                    
                    for session in sessions:
                        try:
                            sid = session["id"]
                            # 현재 세션의 initiator/target 확인
                            curr_initiator = session["initiator_user_id"]
                            curr_target = session["target_user_id"]
                            
                            # 역할 스왑: 재조율 요청자(user_id)가 initiator가 되고, 상대방이 target이 됨
                            # 이렇게 해야 상대방의 홈 화면(Pending Requests)에 카드가 뜸
                            new_initiator = user_id
                            new_target = curr_target if curr_initiator == user_id else curr_initiator
                            
                            # details 업데이트 내용 구성
                            old_details = session.get("details", {})
                            new_details = {
                                **old_details,
                                "purpose": proposal.get('activity', old_details.get('purpose')),
                                "location": proposal.get('location', old_details.get('location')),
                                "participants": old_details.get('participants', []),
                                "proposedDate": proposal.get('date', old_details.get('proposedDate')),
                                "proposedTime": proposal.get('time', old_details.get('proposedTime')),
                                "originalProposedDate": old_details.get('proposedDate') if 'originalProposedDate' not in old_details else old_details.get('originalProposedDate'),
                                "originalProposedTime": old_details.get('proposedTime') if 'originalProposedTime' not in old_details else old_details.get('originalProposedTime'),
                                "rescheduleReason": proposal.get('reason'),
                                "note": proposal.get('manual_input'),
                                "preferredTime": proposal.get('preferred_time'),
                                "proposer": user_name # 제안자 이름 업데이트
                            }
                            
                            # 5. DB 업데이트 (in_progress로 변경, initiator/target 교체, details 업데이트)
                            print(f"🔄 Rescheduling Session: {sid}")
                            print(f"   - Old Initiator: {curr_initiator}, Old Target: {curr_target}")
                            print(f"   - New Initiator: {new_initiator}, New Target: {new_target}")
                            print(f"   - New Details: {new_details}")

                            update_data = {
                                "status": "in_progress",
                                "initiator_user_id": new_initiator,
                                "target_user_id": new_target,
                                "place_pref": new_details,  # Changed from 'details' to 'place_pref'
                                "updated_at": dt_datetime.now().isoformat()
                            }
                            
                            # ⚠️ 중요: 모든 관련 세션 업데이트
                            result = supabase.table('a2a_session').update(update_data).eq('id', sid).execute()
                            print(f"✅ Update Result: {result.data if result.data else 'No Data'}")

                            # [REMOVED] 채팅방 알림 메시지 전송 - dead code (A2A 화면으로 대체됨)

                        except Exception as e:
                            logger.error(f"세션 {session.get('id')} 업데이트 중 오류: {e}")

                    return {
                        "status": 200, 
                        "message": "기존 약속 내용을 변경하여 재요청했습니다.",
                        "updated_session_id": sessions[0]["id"] if sessions else None
                    }

                # ========================================================
                # 거절(방 나가기) 로직 - 세션 삭제 대신 참여자 목록에서 제거
                # ========================================================
                
                from src.chat.chat_repository import ChatRepository
                
                reject_msg = f"{user_name}님이 약속에서 나갔습니다."
                
                # [중요] thread_id가 있으면 해당 thread의 모든 세션을 업데이트해야 함
                # 각 참여자가 서로 다른 세션 ID를 보고 있기 때문
                all_thread_sessions = sessions  # 기본: 전달받은 세션들
                
                # thread_id 추출하여 모든 관련 세션 조회
                first_session = sessions[0] if sessions else {}
                first_place_pref = first_session.get("place_pref", {})
                if isinstance(first_place_pref, str):
                    import json
                    try:
                        first_place_pref = json.loads(first_place_pref)
                    except:
                        first_place_pref = {}
                
                session_thread_id = first_place_pref.get("thread_id")
                if session_thread_id:
                    # thread_id로 모든 세션 조회
                    all_thread_sessions = await A2ARepository.get_thread_sessions(session_thread_id)
                    logger.info(f"🔴 [거절] thread_id={session_thread_id}, 모든 세션 수: {len(all_thread_sessions)}")
                
                # 1. 모든 세션에서 left_participants 수집 후 현재 사용자 추가
                global_left_participants = set()
                for session in all_thread_sessions:
                    sp = session.get("place_pref", {})
                    if isinstance(sp, str):
                        try: sp = json.loads(sp)
                        except: sp = {}
                    for lp in sp.get("left_participants", []):
                        global_left_participants.add(str(lp))
                
                # 현재 거절자 추가
                global_left_participants.add(str(user_id))
                global_left_list = list(global_left_participants)
                logger.info(f"🔴 [거절] 전체 나간 참여자: {global_left_list}")
                
                # 2. 모든 세션에 동기화하여 left_participants 업데이트
                for session in all_thread_sessions:
                    try:
                        sid = session["id"]
                        place_pref = session.get("place_pref", {})
                        if isinstance(place_pref, str):
                            try: place_pref = json.loads(place_pref)
                            except: place_pref = {}
                        
                        # participants 리스트에서 거절자 제거
                        participants = place_pref.get("participants", [])
                        if user_id in participants:
                            participants.remove(user_id)
                        
                        # left_participants 동기화
                        place_pref["participants"] = participants
                        place_pref["left_participants"] = global_left_list
                        
                        logger.info(f"🔴 [거절] 세션 {sid} - left_participants 동기화: {global_left_list}")
                        
                        # DB 업데이트 (아직 status는 변경 안 함)
                        supabase.table('a2a_session').update({
                            "place_pref": place_pref,
                            "updated_at": dt_datetime.now().isoformat()
                        }).eq('id', sid).execute()

                    except Exception as e:
                        logger.error(f"세션 {session.get('id')} 참여자 제거 중 오류: {e}")
                
                # 3. 전원 거절 확인 후 모든 세션 상태 업데이트 (루프 밖에서)
                first_session = all_thread_sessions[0] if all_thread_sessions else {}
                first_pref = first_session.get("place_pref", {})
                if isinstance(first_pref, str):
                    try: first_pref = json.loads(first_pref)
                    except: first_pref = {}
                
                initiator_id = first_session.get("initiator_user_id")
                reschedule_requester = first_pref.get("rescheduleRequestedBy")
                actual_requester = str(reschedule_requester) if reschedule_requester else str(initiator_id)
                
                participant_user_ids = first_session.get("participant_user_ids", [])
                if not participant_user_ids:
                    participant_user_ids = [initiator_id, first_session.get("target_user_id")]
                
                non_requester_participants = [p for p in participant_user_ids if str(p) != actual_requester]
                all_others_left = all(str(p) in global_left_participants for p in non_requester_participants)
                
                logger.info(f"🔴 [거절] 요청자: {actual_requester}, 비요청자: {non_requester_participants}, 전원나감: {all_others_left}")
                
                if all_others_left and len(non_requester_participants) > 0:
                    logger.info(f"🔴 [거절] 모든 참여자가 나감 - 전체 {len(all_thread_sessions)}개 세션을 'rejected'로 변경")
                    for session in all_thread_sessions:
                        supabase.table('a2a_session').update({
                            "status": "rejected",
                            "updated_at": dt_datetime.now().isoformat()
                        }).eq('id', session['id']).execute()

                # 2. 시스템 메시지: 남은 참여자들에게 거절 알림 (Loop 밖에서 한 번만 전송)
                # thread_id로 묶여있으므로 하나의 세션에만 추가하면 됨
                if all_thread_sessions:
                    target_session = all_thread_sessions[0]
                    tsid = target_session["id"]
                    # 메시지 수신자는 해당 세션의 상대방 (나 자신 제외)
                    receiver = target_session.get("target_user_id") if target_session.get("target_user_id") != user_id else target_session.get("initiator_user_id")
                    
                    await A2ARepository.add_message(
                        session_id=tsid,
                        sender_user_id=user_id,
                        receiver_user_id=receiver,
                        message_type="schedule_rejection",
                        message={"text": reject_msg, "left_user_id": user_id, "left_user_name": user_name}
                    )

                # 3. chat_log 메타데이터 업데이트 (거절 상태 기록)
                for pid in all_participants:
                    logs_response = supabase.table('chat_log').select('*').eq(
                        'user_id', pid
                    ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1).execute()

                    if logs_response.data:
                        target_log = logs_response.data[0]
                        meta = target_log.get('metadata', {})

                        if meta.get('thread_id') == thread_id:
                            left_users = meta.get('left_users', [])
                            if user_id not in left_users:
                                left_users.append(user_id)
                            new_meta = {
                                **meta,
                                "left_users": left_users,  # 나간 사람 목록
                                "last_left_by": user_id,
                                "last_left_by_name": user_name,
                            }
                            supabase.table('chat_log').update({'metadata': new_meta}).eq('id', target_log['id']).execute()
                
                # 4. 알림 메시지 전송
                for pid in all_participants:
                    if pid == user_id:
                        # 거절한 본인에게는 확인 메시지만 (재조율 유도 X)
                        await ChatRepository.create_chat_log(
                            user_id=pid,
                            request_text=None,
                            response_text=f"해당 약속에서 나갔습니다.",
                            message_type="system"
                        )
                        continue


                    # 원본 채팅 세션 ID 추출 (place_pref 또는 metadata에 저장됨)
                    curr_origin_session_id = None
                    for session in sessions:
                         pp = session.get("place_pref", {})
                         if isinstance(pp, str):
                             try:
                                 pp = json.loads(pp)
                             except:
                                 pp = {}
                         if pp.get("origin_chat_session_id"):
                             curr_origin_session_id = pp.get("origin_chat_session_id")
                             break
                    
                    # [Fallback] origin_chat_session_id가 없으면 initiator의 기본 채팅 세션 조회
                    if not curr_origin_session_id:
                        try:
                            default_session = supabase.table("chat_sessions").select("id").eq(
                                "user_id", pid
                            ).eq("title", "기본 채팅").single().execute()
                            if default_session.data:
                                curr_origin_session_id = default_session.data.get("id")
                                logger.info(f"Initiator({pid})의 기본 채팅 세션 사용: {curr_origin_session_id}")
                        except Exception as e:
                            logger.warning(f"기본 채팅 세션 조회 실패: {e}")
                    
                    # session_id가 있으면 friend_id는 None이어도 됨 (세션에 메시지 추가)
                    # 없으면 기존처럼 friend_id 사용 (1:1 채팅방)
                    target_session_id = curr_origin_session_id if curr_origin_session_id else None
                    target_friend_id = user_id if not target_session_id else None

                    # 상대방에게 알림 전송 (재조율 자동 트리거 X)
                    await ChatRepository.create_chat_log(
                        user_id=pid,
                        request_text=None,
                        response_text=f"{user_name}님이 약속에서 나갔습니다.",
                        friend_id=target_friend_id,
                        session_id=target_session_id,
                        message_type="schedule_rejection",
                        metadata={
                            "left_user_id": user_id,
                            "left_user_name": user_name,
                            "thread_id": thread_id,
                            "session_ids": session_ids,
                            "schedule_date": proposal.get("date"),
                            "schedule_time": proposal.get("time"),
                            "schedule_activity": proposal.get("activity"),
                            "schedule_location": proposal.get("location"),
                        }
                    )
                    
                return {"status": 200, "message": "약속에서 나갔습니다."}

        except Exception as e:
            logger.error(f"승인 핸들러 오류: {str(e)}", exc_info=True)
            return {"status": 500, "error": str(e)}

    # [REMOVED] _send_approval_request_to_chat 함수 - dead code (A2A 화면과 Home 알림으로 대체됨)
