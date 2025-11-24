from typing import Dict, Any, Optional
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from .repository import A2ARepository
from src.auth.repository import AuthRepository
from src.calendar.service import GoogleCalendarService
from src.auth.service import AuthService
from config.settings import settings
from config.database import supabase
import httpx
import datetime as dt

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
