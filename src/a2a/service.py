from typing import Dict, Any, Optional, List
import logging
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from .repository import A2ARepository
from src.auth.repository import AuthRepository
from src.calendar.service import GoogleCalendarService
from src.auth.service import AuthService
from config.settings import settings
from config.database import supabase
import httpx
import datetime as dt
from datetime import datetime as dt_datetime

logger = logging.getLogger(__name__)

class A2AService:
    
    @staticmethod
    async def start_a2a_session(
        initiator_user_id: str,
        target_user_id: str,
        summary: Optional[str] = None,
        duration_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        A2A 세션 시작 및 전체 시뮬레이션 자동 진행
        백엔드에서 모든 단계를 자동으로 처리
        """
        try:
            # 1) 세션 생성 (summary는 place_pref에 포함)
            session = await A2ARepository.create_session(
                initiator_user_id=initiator_user_id,
                target_user_id=target_user_id,
                intent="schedule",
                place_pref={"summary": summary or f"일정 조율"} if summary else None
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
            
            # 3) 단계별 시뮬레이션 실행
            result = await A2AService._execute_a2a_simulation(
                session_id=session_id,
                initiator_user_id=initiator_user_id,
                target_user_id=target_user_id,
                initiator_name=initiator_name,
                target_name=target_name,
                summary=summary or f"{target_name}와 약속",
                duration_minutes=duration_minutes
            )
            
            # 4) 세션 완료
            await A2ARepository.update_session_status(session_id, "completed")
            
            return {
                "status": 200,
                "session_id": session_id,
                "event": result.get("event"),
                "messages": result.get("messages", [])
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
        
        # 단계 1: 내 에이전트가 상대 일정 확인 시작
        msg1 = {
            "text": f"{target_name}님의 일정을 확인 중입니다...",
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
        await asyncio.sleep(0.5)  # 자연스러운 딜레이
        
        # 단계 2: 상대 에이전트가 일정 확인 중
        msg2_checking = {
            "text": f"{initiator_name}님의 일정을 확인하고 있습니다.",
            "step": 2
        }
        await A2ARepository.add_message(
            session_id=session_id,
            sender_user_id=target_user_id,
            receiver_user_id=initiator_user_id,
            message_type="agent_reply",
            message=msg2_checking
        )
        messages_log.append(msg2_checking["text"])
        await asyncio.sleep(0.5)
        
        # 단계 3: 상대 에이전트가 일정 확인 완료 및 상세 정보 제공
        msg2_done = {
            "text": f"확인 완료했습니다. {initiator_name}님의 캘린더를 확인했습니다.",
            "step": 2.5
        }
        await A2ARepository.add_message(
            session_id=session_id,
            sender_user_id=target_user_id,
            receiver_user_id=initiator_user_id,
            message_type="agent_reply",
            message=msg2_done
        )
        messages_log.append(msg2_done["text"])
        await asyncio.sleep(0.5)
        
        # 단계 3: 공통 가용 시간 계산
        try:
            # Google Calendar 토큰 확보
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
                # 공통 시간이 없는 경우
                msg_no_slot = {
                    "text": "공통으로 비는 시간을 찾지 못했습니다.",
                    "step": 3
                }
                await A2ARepository.add_message(
                    session_id=session_id,
                    sender_user_id=initiator_user_id,
                    receiver_user_id=target_user_id,
                    message_type="system",
                    message=msg_no_slot
                )
                return {
                    "status": "no_slots",
                    "messages": messages_log
                }
            
            # 가장 이른 슬롯 선택
            earliest_slot = slots[0]
            slot_start = earliest_slot["start"]
            slot_end = earliest_slot["end"]
            
            # 시간 포맷팅 (한국 시간)
            start_dt = datetime.fromisoformat(slot_start.replace("Z", "+00:00"))
            start_kst = start_dt.astimezone(timezone(timedelta(hours=9)))
            time_str = start_kst.strftime("%m월 %d일 %H시")
            
            # 단계 4: 공통 시간 제안 및 장소 논의
            time_str_detail = start_kst.strftime("%Y년 %m월 %d일 %H시 %M분")
            msg3_proposal = {
                "text": f"공통으로 비는 시간을 찾았습니다: {time_str_detail}",
                "step": 3,
                "proposed_time": slot_start
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=initiator_user_id,
                receiver_user_id=target_user_id,
                message_type="proposal",
                message=msg3_proposal
            )
            messages_log.append(msg3_proposal["text"])
            await asyncio.sleep(0.5)
            
            # 단계 4.5: 장소 제안 (기본 장소 또는 사용자 입력 장소)
            location_suggestion = summary.split(" ")[-1] if "에서" in summary or "장소" in summary else None
            if not location_suggestion:
                location_suggestion = "만날 장소를 정해주세요"
            
            msg3_location = {
                "text": f"만날 장소는 어떻게 하시겠어요? {location_suggestion if location_suggestion != '만날 장소를 정해주세요' else '제안해주시면 반영하겠습니다.'}",
                "step": 3.5
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=initiator_user_id,
                receiver_user_id=target_user_id,
                message_type="proposal",
                message=msg3_location
            )
            messages_log.append(msg3_location["text"])
            await asyncio.sleep(0.5)
            
            # 단계 5: 상대 에이전트가 시간 및 장소 확인
            msg4_confirm = {
                "text": f"{time_str_detail}에 만나는 것으로 확인했습니다. 장소는 나중에 정해도 될까요?",
                "step": 4
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=target_user_id,
                receiver_user_id=initiator_user_id,
                message_type="confirm",
                message=msg4_confirm
            )
            messages_log.append(msg4_confirm["text"])
            await asyncio.sleep(0.5)
            
            # 단계 5.5: 내 에이전트가 장소 확정
            msg4_location_ok = {
                "text": "네, 장소는 나중에 정해도 됩니다. 일정 확정하겠습니다.",
                "step": 4.5
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=initiator_user_id,
                receiver_user_id=target_user_id,
                message_type="confirm",
                message=msg4_location_ok
            )
            messages_log.append(msg4_location_ok["text"])
            await asyncio.sleep(0.5)
            
            # 단계 6: 일정 생성 (양쪽 캘린더에 모두 추가)
            me_email = initiator.get("email")
            friend_email = target.get("email")
            
            from src.calendar.models import CreateEventRequest
            
            # 장소 추출 (summary에서 "에서" 또는 "장소" 키워드 찾기)
            location = None
            if "에서" in summary:
                location = summary.split("에서")[-1].strip()
            elif "장소" in summary:
                location = summary.split("장소")[-1].strip()
            
            # 내 캘린더에 이벤트 생성
            event_req = CreateEventRequest(
                summary=summary,
                start_time=slot_start,
                end_time=slot_end,
                location=location,
                attendees=[e for e in [me_email, friend_email] if e],
            )
            
            event = await service.create_calendar_event(
                access_token=me_access,
                event_data=event_req,
            )
            
            # 상대방 캘린더에도 직접 이벤트 생성 (더 확실하게)
            try:
                friend_event = await service.create_calendar_event(
                    access_token=friend_access,
                    event_data=event_req,
                )
                logger.info(f"상대방 캘린더에도 이벤트 생성 성공: {friend_event.id}")
            except Exception as e:
                logger.warning(f"상대방 캘린더 이벤트 생성 실패 (attendees로 초대는 전송됨): {str(e)}")
                # 실패해도 attendees로 초대는 전송되므로 계속 진행
            
            # calendar_event에 session_id 연결 및 a2a_session에 final_event_id 업데이트
            # event.id는 Google Calendar의 event ID (google_event_id)
            if event and event.id:
                # calendar_event 테이블에 저장 (없으면 생성)
                await A2AService._save_calendar_event_to_db(
                    session_id=session_id,
                    owner_user_id=initiator_user_id,
                    google_event_id=event.id,
                    summary=summary,
                    location=location,
                    start_at=slot_start,
                    end_at=slot_end,
                    html_link=event.htmlLink
                )
                # 양방향 연결 (calendar_event.session_id, a2a_session.final_event_id)
                await A2ARepository.link_calendar_event(session_id, event.id)
            
            # 단계 7: 최종 완료 메시지
            location_text = f" 장소: {location}" if location else ""
            msg5 = {
                "text": f"일정이 확정되었습니다! {time_str_detail}{location_text}\n양쪽 캘린더에 모두 추가되었습니다.",
                "step": 5
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=initiator_user_id,
                receiver_user_id=target_user_id,
                message_type="final",
                message=msg5
            )
            messages_log.append(msg5["text"])
            
            # 단계 7.5: 상대 에이전트 확인 메시지
            msg5_confirm = {
                "text": "네, 확인했습니다. 즐거운 만남 되세요!",
                "step": 5.5
            }
            await A2ARepository.add_message(
                session_id=session_id,
                sender_user_id=target_user_id,
                receiver_user_id=initiator_user_id,
                message_type="final",
                message=msg5_confirm
            )
            messages_log.append(msg5_confirm["text"])
            
            return {
                "status": "completed",
                "event": event,
                "messages": messages_log
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
            from src.auth.repository import AuthRepository
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
            from src.auth.repository import AuthRepository
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
        duration_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        다중 사용자 일정 조율 세션 시작
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
                    existing_session_map[target_id] = existing_session
                    all_existing_sessions.append(existing_session)
            
            # 기존 세션이 하나라도 있고, 진행 중이거나 최근에 생성된 경우 재사용
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
                                "location": location or place_pref.get("location"),
                                "activity": activity or place_pref.get("activity")
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
                        place_pref = {
                            "summary": summary,
                            "thread_id": thread_id,
                            "participants": target_user_ids,
                            "location": location,
                            "activity": activity
                        }
                        session = await A2ARepository.create_session(
                            initiator_user_id=initiator_user_id,
                            target_user_id=target_id,
                            intent="schedule",
                            place_pref=place_pref,
                            time_window={"date": date, "time": time, "duration_minutes": duration_minutes} if date or time else None
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
                    place_pref = {
                        "summary": summary,
                        "thread_id": thread_id,
                        "participants": target_user_ids,
                        "location": location,
                        "activity": activity
                    }
                    
                    session = await A2ARepository.create_session(
                        initiator_user_id=initiator_user_id,
                        target_user_id=target_id,
                        intent="schedule",
                        place_pref=place_pref,
                        time_window={"date": date, "time": time, "duration_minutes": duration_minutes} if date or time else None
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
            result = await A2AService._execute_multi_user_coordination(
                thread_id=thread_id,
                sessions=sessions,
                initiator_user_id=initiator_user_id,
                initiator_name=initiator_name,
                date=date,
                time=time,
                location=location,
                activity=activity,
                duration_minutes=duration_minutes,
                reuse_existing=reuse_existing  # 기존 세션 재사용 여부 전달
            )
            
            # 4) 모든 세션 완료 처리 (기존 세션 재사용 시에도 상태 업데이트)
            for session_info in sessions:
                # completed 상태로 변경하지 않고, in_progress 유지 (대화가 계속될 수 있음)
                # 필요시에만 completed로 변경
                pass
            
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
        
        try:
            # 기존 세션 재사용 시, 기존 메시지가 있으면 건너뛰고 새 요청만 추가
            if not reuse_existing:
                # 1) 초기 메시지: 요청자 Agent가 모든 참여자에게 알림 (새 세션인 경우만)
                request_text = f"{date or '일정'} {time or ''}에 {initiator_name}님이 약속을 요청했습니다."
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
            
            # 2) 각 참여자의 Agent가 자신의 캘린더 확인
            availability_results = []
            
            for session_info in sessions:
                target_id = session_info["target_id"]
                target_name = session_info["target_name"]
                
                # "사용자의 일정을 확인 중입니다..." 메시지
                checking_msg = "사용자의 일정을 확인 중입니다..."
                await A2ARepository.add_message(
                    session_id=session_info["session_id"],
                    sender_user_id=target_id,
                    receiver_user_id=initiator_user_id,
                    message_type="agent_query",
                    message={"text": checking_msg}
                )
                messages.append({
                    "session_id": session_info["session_id"],
                    "sender": f"{target_name}봇",
                    "text": checking_msg
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
            
            # 3) 시간이 지정된 경우: 가능 여부 확인
            if date and time:
                all_available = all(r["available"] for r in availability_results)
                
                if all_available:
                    # 모두 가능하면 확정 제안
                    proposal_text = f"{target_name}님은 {date} {time} 가능합니다."
                    for result in availability_results:
                        await A2ARepository.add_message(
                            session_id=result["session_id"],
                            sender_user_id=result["user_id"],
                            receiver_user_id=initiator_user_id,
                            message_type="agent_reply",
                            message={"text": proposal_text.replace(target_name, result["user_name"])}
                        )
                    
                    # 장소 제안은 제거 (사용자가 직접 지정한 경우만)
                    if location:
                        location_msg = f"장소는 {location} 어떠세요?"
                        first_target = availability_results[0]
                        await A2ARepository.add_message(
                            session_id=first_target["session_id"],
                            sender_user_id=first_target["user_id"],
                            receiver_user_id=initiator_user_id,
                            message_type="proposal",
                            message={"text": location_msg}
                        )
                        
                        # 다른 참여자들의 동의
                        for result in availability_results[1:]:
                            await A2ARepository.add_message(
                                session_id=result["session_id"],
                                sender_user_id=result["user_id"],
                                receiver_user_id=initiator_user_id,
                                message_type="confirm",
                                message={"text": "네, 괜찮습니다."}
                            )
                    
                    # 요청자 Agent의 최종 확인
                    final_msg = "일정 확정 채팅 전달하겠습니다."
                    for session_info in sessions:
                        await A2ARepository.add_message(
                            session_id=session_info["session_id"],
                            sender_user_id=initiator_user_id,
                            receiver_user_id=session_info["target_id"],
                            message_type="final",
                            message={"text": final_msg}
                        )
                    
                    # 승인 필요 플래그 설정
                    # 상대방들에게 승인 요청 메시지 전송
                    proposal_data = {
                        "date": date,
                        "time": time,
                        "location": location or None,
                        "participants": [r["user_name"] for r in availability_results]
                    }
                    
                    for result in availability_results:
                        target_id = result["user_id"]
                        # 상대방의 Chat 화면에 승인 요청 메시지 전송
                        await A2AService._send_approval_request_to_chat(
                            user_id=target_id,
                            thread_id=thread_id,
                            session_ids=[s["session_id"] for s in sessions],
                            proposal=proposal_data,
                            initiator_name=initiator_name
                        )
                    
                    return {
                        "messages": messages,
                        "needs_approval": True,
                        "proposal": proposal_data
                    }
                else:
                    # 일부 불가능하면 재조율 필요
                    unavailable_users = [r["user_name"] for r in availability_results if not r["available"]]
                    reject_msg = f"{', '.join(unavailable_users)}님이 해당 시간에 일정이 있어 재조율이 필요합니다."
                    
                    for result in availability_results:
                        if not result["available"]:
                            await A2ARepository.add_message(
                                session_id=result["session_id"],
                                sender_user_id=result["user_id"],
                                receiver_user_id=initiator_user_id,
                                message_type="agent_reply",
                                message={"text": f"{result['user_name']}님은 해당 시간에 일정이 있습니다."}
                            )
                    
                    return {
                        "messages": messages,
                        "needs_approval": False,
                        "needs_recoordination": True,
                        "unavailable_users": unavailable_users
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
                # 시간이 지정되지 않으면 가능한 시간 슬롯 반환
                return {
                    "available": True,
                    "available_slots": [
                        {"date": "9월 3일", "time": "오후 4시"},
                        {"date": "9월 4일", "time": "오후 5시"},
                        {"date": "9월 5일", "time": "오후 7시"}
                    ]
                }
            
            # 날짜/시간 파싱 (ChatService의 파싱 로직 활용)
            from src.chat.service import ChatService
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
                        if days_ahead <= 0:
                            days_ahead += 7
                        parsed_date = today + timedelta(days=days_ahead)
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
                            if parsed_time < event_end_dt and end_time > event_start_dt:
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
        일정 승인/거절 처리
        - 승인: 모든 참여자가 승인한 후에만 캘린더에 일정 추가
        - 거절: 재조율 시작 (가능한 시간 확인)
        """
        try:
            # 세션 정보 조회
            sessions = []
            for session_id in session_ids:
                session = await A2ARepository.get_session(session_id)
                if session:
                    sessions.append(session)
            
            if not sessions:
                return {"status": 404, "error": "세션을 찾을 수 없습니다."}
            
            # 사용자 정보 조회
            user = await AuthRepository.find_user_by_id(user_id)
            user_name = user.get("name", "사용자") if user else "사용자"
            
            # 모든 참여자 ID 수집
            all_participants = set()
            for session in sessions:
                all_participants.add(session.get("initiator_user_id"))
                all_participants.add(session.get("target_user_id"))
            
            if approved:
                # 승인 상태 저장 (thread_id 기준)
                # 승인 요청 메시지의 metadata에서 승인 상태 확인 및 업데이트
                try:
                    from config.database import supabase
                    from src.chat.repository import ChatRepository
                    
                    # 승인 요청 메시지 조회하여 현재 승인 상태 확인 (thread_id 기준)
                    approval_logs = []
                    for participant_id in all_participants:
                        response = supabase.table('chat_log').select('*').eq(
                            'user_id', participant_id
                        ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(10).execute()
                        
                        # thread_id가 일치하는 메시지 찾기
                        for log in response.data or []:
                            metadata = log.get('metadata', {})
                            if metadata.get('thread_id') == thread_id:
                                approval_logs.append({
                                    'user_id': participant_id,
                                    'log': log
                                })
                                break
                    
                    # 현재 승인한 사용자 수집 (approved_by_list에서)
                    approved_users = set()
                    for log_data in approval_logs:
                        metadata = log_data['log'].get('metadata', {})
                        approved_list = metadata.get('approved_by_list', [])
                        if isinstance(approved_list, list):
                            approved_users.update(approved_list)
                        elif metadata.get('approved_by'):
                            # 기존 방식 호환
                            approved_users.add(metadata['approved_by'])
                    
                    # 현재 사용자 승인 추가
                    approved_users.add(user_id)
                    
                    # 모든 참여자가 승인했는지 확인
                    all_approved = len(approved_users) == len(all_participants)
                    
                    # 승인 메시지 추가
                    approval_msg = f"{user_name}님이 일정을 승인했습니다."
                    if all_approved:
                        approval_msg += " 모든 참여자가 승인하여 캘린더에 일정을 추가하겠습니다."
                    else:
                        remaining = len(all_participants) - len(approved_users)
                        approval_msg += f" ({remaining}명의 승인 대기 중)"
                    
                    for session in sessions:
                        await A2ARepository.add_message(
                            session_id=session["id"],
                            sender_user_id=user_id,
                            receiver_user_id=session.get("target_user_id") if session.get("target_user_id") != user_id else session.get("initiator_user_id"),
                            message_type="confirm",
                            message={"text": approval_msg}
                        )
                    
                    # 승인 요청 메시지의 metadata 업데이트 (모든 참여자의 메시지에 동기화)
                    # 모든 참여자의 승인 요청 메시지를 찾아서 approved_by_list 동기화
                    all_approval_logs = []
                    for participant_id in all_participants:
                        response = supabase.table('chat_log').select('*').eq(
                            'user_id', participant_id
                        ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(10).execute()
                        
                        for log in response.data or []:
                            metadata = log.get('metadata', {})
                            if metadata.get('thread_id') == thread_id:
                                all_approval_logs.append({
                                    'user_id': participant_id,
                                    'log': log
                                })
                                break
                    
                    # 모든 승인 요청 메시지에 동일한 approved_by_list 업데이트
                    updated_approved_list = list(approved_users)
                    for log_data in all_approval_logs:
                        existing_metadata = log_data['log'].get('metadata', {})
                        supabase.table('chat_log').update({
                            'metadata': {
                                **existing_metadata,
                                'approved_by': user_id,
                                'approved_by_list': updated_approved_list,
                                'approved_at': dt_datetime.now().isoformat(),
                                'all_approved': all_approved
                            }
                        }).eq('id', log_data['log']['id']).execute()
                    
                    # 모든 참여자가 승인한 경우에만 캘린더에 일정 추가
                    if all_approved:
                        date = proposal.get("date")
                        time = proposal.get("time")
                        location = proposal.get("location", "")
                        participants = proposal.get("participants", [])
                        
                        # 날짜/시간 파싱
                        from src.chat.service import ChatService
                        from zoneinfo import ZoneInfo
                        KST = ZoneInfo("Asia/Seoul")
                        
                        # 시간 파싱 (ChatService의 로직 활용)
                        parsed_time = await ChatService.parse_time_string(time, f"{date} {time}")
                        if parsed_time:
                            start_time = parsed_time['start_time']
                            end_time = parsed_time['end_time']
                        else:
                            # 파싱 실패 시 기본값
                            from datetime import timedelta
                            start_time = datetime.now(KST) + timedelta(days=1)
                            end_time = start_time + timedelta(hours=1)
                        
                        # 모든 참여자의 이메일 수집
                        participant_emails = []
                        for participant_id in all_participants:
                            participant_user = await AuthRepository.find_user_by_id(participant_id)
                            if participant_user and participant_user.get("email"):
                                participant_emails.append(participant_user["email"])
                        
                        # 모든 참여자 캘린더에 일정 추가
                        event_ids = []
                        for participant_id in all_participants:
                            access_token = await A2AService._ensure_access_token_by_user_id(participant_id)
                            if access_token:
                                from src.calendar.service import CreateEventRequest
                                from src.calendar.service import GoogleCalendarService
                                
                                summary = f"{', '.join(participants)}와의 미팅"
                                if location:
                                    summary += f" ({location})"
                                
                                event_req = CreateEventRequest(
                                    summary=summary,
                                    start_time=start_time.isoformat(),
                                    end_time=end_time.isoformat(),
                                    location=location,
                                    attendees=participant_emails  # 모든 참여자 이메일 추가
                                )
                                
                                google_calendar = GoogleCalendarService()
                                event = await google_calendar.create_calendar_event(
                                    access_token=access_token,
                                    event_data=event_req
                                )
                                
                                if event:
                                    # calendar_event 테이블에 저장
                                    # 세션 ID는 첫 번째 세션 사용
                                    session_id = sessions[0]["id"] if sessions else None
                                    await A2AService._save_calendar_event_to_db(
                                        session_id=session_id,
                                        owner_user_id=participant_id,
                                        google_event_id=event.id,
                                        summary=summary,
                                        location=location,
                                        start_at=start_time.isoformat(),
                                        end_at=end_time.isoformat(),
                                        html_link=event.htmlLink
                                    )
                                    
                                    # 세션에 연결
                                    if session_id:
                                        await A2ARepository.link_calendar_event(session_id, event.id)
                                    event_ids.append(event.id)
                        
                        return {
                            "status": 200,
                            "message": "모든 참여자가 승인하여 일정이 확정되었습니다. 모든 참여자 캘린더에 일정을 추가했습니다.",
                            "event_ids": event_ids,
                            "all_approved": True
                        }
                    else:
                        return {
                            "status": 200,
                            "message": approval_msg,
                            "all_approved": False,
                            "remaining_approvals": len(all_participants) - len(approved_users)
                        }
                        
                except Exception as e:
                    logger.error(f"승인 처리 중 오류: {str(e)}", exc_info=True)
                    return {
                        "status": 500,
                        "error": f"승인 처리 실패: {str(e)}"
                    }
            else:
                # 거절: 재조율 시작 (가능한 시간 확인)
                reject_msg = f"{user_name}님이 일정을 거절했습니다. 재조율을 진행하겠습니다."
                
                for session in sessions:
                    await A2ARepository.add_message(
                        session_id=session["id"],
                        sender_user_id=user_id,
                        receiver_user_id=session.get("target_user_id") if session.get("target_user_id") != user_id else session.get("initiator_user_id"),
                        message_type="agent_reply",
                        message={"text": reject_msg}
                    )
                
                # 거절한 사용자의 가능한 시간 확인
                date = proposal.get("date")
                time = proposal.get("time")
                duration_minutes = proposal.get("duration_minutes", 60)
                
                # 가능한 시간 슬롯 조회
                availability = await A2AService._check_user_availability(
                    user_id=user_id,
                    date=date,
                    time=None,  # 시간 미지정으로 가능한 시간 슬롯 조회
                    duration_minutes=duration_minutes
                )
                
                available_slots = availability.get("available_slots", [])
                if not available_slots:
                    # 가능한 시간이 없으면 일반적인 시간 제안
                    available_slots = [
                        {"date": "내일", "time": "오후 2시"},
                        {"date": "내일", "time": "오후 4시"},
                        {"date": "모레", "time": "오전 10시"}
                    ]
                
                # 가능한 시간을 메시지로 구성
                slots_text = "\n".join([f"- {slot.get('date', '')} {slot.get('time', '')}" for slot in available_slots[:5]])
                recoordination_msg = f"{reject_msg}\n\n가능한 시간 후보:\n{slots_text}\n\n어떤 시간이 가능하신가요?"
                
                # 거절 메시지를 모든 참여자의 chat_log에 저장
                try:
                    from src.chat.repository import ChatRepository
                    for participant_id in all_participants:
                        # chat_log에 거절 및 재조율 메시지 저장
                        await ChatRepository.create_chat_log(
                            user_id=participant_id,
                            request_text=None,
                            response_text=recoordination_msg,
                            friend_id=None,
                            message_type="schedule_rejection",
                            metadata={
                                "rejected_by": user_id,
                                "rejected_at": dt_datetime.now().isoformat(),
                                "proposal": proposal,
                                "thread_id": thread_id,
                                "session_ids": session_ids,
                                "needs_recoordination": True,
                                "available_slots": available_slots
                            }
                        )
                        
                        # 승인 요청 메시지의 metadata 업데이트 (버튼 숨기기)
                        from config.database import supabase
                        supabase.table('chat_log').update({
                            'metadata': {
                                'needs_approval': False,
                                'rejected_by': user_id,
                                'rejected_at': dt_datetime.now().isoformat(),
                                'proposal': proposal,
                                'thread_id': thread_id,
                                'session_ids': session_ids
                            }
                        }).eq('user_id', participant_id).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1).execute()
                except Exception as e:
                    logger.warning(f"거절 메시지 저장 실패: {str(e)}")
                
                # 재조율 로직
                return {
                    "status": 200,
                    "message": recoordination_msg,
                    "needs_recoordination": True,
                    "available_slots": available_slots
                }
                
        except Exception as e:
            logger.error(f"일정 승인 처리 실패: {str(e)}", exc_info=True)
            return {
                "status": 500,
                "error": f"일정 승인 처리 실패: {str(e)}"
            }
    
    @staticmethod
    async def _send_approval_request_to_chat(
        user_id: str,
        thread_id: str,
        session_ids: List[str],
        proposal: Dict[str, Any],
        initiator_name: str
    ):
        """
        상대방의 Chat 화면에 승인 요청 메시지 전송
        """
        try:
            from src.chat.repository import ChatRepository
            
            date_str = proposal.get("date", "")
            time_str = proposal.get("time", "")
            location_str = proposal.get("location", "")
            participants_str = ", ".join(proposal.get("participants", []))
            
            approval_message = f"✅ 약속 확정: {date_str} {time_str}"
            if location_str:
                approval_message += f" / {location_str}"
            approval_message += f"\n참여자: {participants_str}\n확정하시겠습니까?"
            
            # chat_log에 승인 요청 메시지 저장
            # friend_id는 initiator_id로 설정 (요청자와의 대화로 표시)
            # 실제로는 thread_id를 사용하여 모든 참여자와의 대화로 표시해야 함
            # metadata에 승인에 필요한 정보 저장
            await ChatRepository.create_chat_log(
                user_id=user_id,
                request_text=None,
                response_text=approval_message,
                friend_id=None,  # 다중 참여자이므로 None
                message_type="schedule_approval",
                metadata={
                    "proposal": proposal,
                    "thread_id": thread_id,
                    "session_ids": session_ids,
                    "needs_approval": True
                }
            )
            
            logger.info(f"승인 요청 메시지 전송 완료: user_id={user_id}, thread_id={thread_id}")
            
        except Exception as e:
            logger.error(f"승인 요청 메시지 전송 실패: {str(e)}")
