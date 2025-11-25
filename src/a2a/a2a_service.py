from typing import Dict, Any, Optional, List
import logging
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from .a2a_repository import A2ARepository
from src.auth.auth_repository import AuthRepository
from src.calendar.calender_service import GoogleCalendarService
from src.auth.auth_service import AuthService
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
            
            # 4) 승인 필요 시 chat_log에 승인 요청 메시지 추가
            if result.get("needs_approval") and result.get("proposal"):
                proposal = result.get("proposal")
                
                # 시뮬레이션 결과로 나온 시간에 대해 요청자와 타겟 모두 가능한지 최종 체크
                check_initiator = await A2AService._check_user_availability(
                    initiator_user_id, proposal["date"], proposal["time"], duration_minutes
                )
                check_target = await A2AService._check_user_availability(
                    target_user_id, proposal["date"], proposal["time"], duration_minutes
                )

                # 둘 다 가능할 때만 승인 요청 카드 발송
                if check_initiator["available"] and check_target["available"]:
                    await A2AService._send_approval_request_to_chat(
                        user_id=initiator_user_id,
                        thread_id=None,
                        session_ids=[session_id],
                        proposal=proposal,
                        initiator_name=initiator_name
                    )
                    await A2AService._send_approval_request_to_chat(
                        user_id=target_user_id,
                        thread_id=None,
                        session_ids=[session_id],
                        proposal=proposal,
                        initiator_name=initiator_name
                    )
                else:
                    # 시간이 그새 찼다면? needs_approval 취소 및 재조율 메시지 (예외 처리)
                    # 여기서는 간단히 로그만 남기고, 실제로는 재조율 로직을 탈 수 있음
                    logger.warning("승인 요청 직전 일정이 차버림. 카드 발송 취소.")
                    result["needs_approval"] = False
                    # 사용자에게 알림 메시지 추가 로직 필요 시 여기에 구현
            
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
        
        # 단계 1: 내 캘린더 확인 중
        msg1 = {
            "text": f"내 캘린더 확인 중...",
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
        msg3_checking = {
            "text": f"{initiator_name}님의 일정을 확인하고 있습니다.",
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
        msg4_done = {
            "text": f"확인 완료했습니다. {initiator_name}님의 캘린더를 확인했습니다.",
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
                msg_no_slot = {
                    "text": "공통으로 비는 시간을 찾지 못했습니다. 각자의 가능한 시간을 확인 중...",
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
                msg_recoordination = {
                    "text": "공통 시간이 없어 각자의 가능한 시간을 제안했습니다. 사용자에게 확인을 요청하겠습니다.",
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
            msg5_proposal = {
                "text": f"공통으로 비는 시간을 찾았습니다: {time_str_detail}",
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
            msg6_confirm = {
                "text": f"{time_str_detail}에 만나는 것으로 확인했습니다.",
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
                "text": "사용자 승인을 기다리는 중...",
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
            
            # 2) 요청자 포함 모든 참여자의 Agent가 자신의 캘린더 확인
            availability_results = []
            
            # 먼저 요청자의 일정 확인
            initiator_checking_msg = f"{initiator_name}님의 일정을 확인 중입니다..."
            for session_info in sessions:
                await A2ARepository.add_message(
                    session_id=session_info["session_id"],
                    sender_user_id=initiator_user_id,
                    receiver_user_id=session_info["target_id"],
                    message_type="agent_query",
                    message={"text": initiator_checking_msg, "step": 1}
                )
            messages.append({
                "sender": f"{initiator_name}봇",
                "text": initiator_checking_msg
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
                checking_msg = f"{target_name}님의 일정을 확인 중입니다..."
                await A2ARepository.add_message(
                    session_id=session_info["session_id"],
                    sender_user_id=target_id,
                    receiver_user_id=initiator_user_id,
                    message_type="agent_query",
                    message={"text": checking_msg, "step": 2}
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
            
            # 3) 시간이 지정된 경우: 모든 참여자(요청자 포함) 가능 여부 확인
            if date and time:
                all_available = all(r.get("available", False) and not r.get("error") for r in availability_results)
                
                if all_available:
                    # 모든 참여자(요청자 포함)가 가능하면 확정 제안
                    # 공통 시간 확인 완료 메시지
                    common_time_msg = f" 모든 참여자의 일정을 확인했습니다. {date} {time}에 모두 가능합니다."
                    for session_info in sessions:
                        await A2ARepository.add_message(
                            session_id=session_info["session_id"],
                            sender_user_id=initiator_user_id,
                            receiver_user_id=session_info["target_id"],
                            message_type="agent_reply",
                            message={"text": common_time_msg, "step": 3}
                        )
                    
                    # 참여자 목록 (요청자 포함)
                    all_participant_names = [r["user_name"] for r in availability_results]
                    proposal_data = {
                        "date": date,
                        "time": time,
                        "location": location or None,
                        "participants": all_participant_names,
                        "start_time": None,  # 시간 파싱 필요
                        "end_time": None
                    }
                    
                    # 시간 파싱 (proposal에 start_time, end_time 추가)
                    try:
                        from src.chat.chat_service import ChatService
                        from zoneinfo import ZoneInfo
                        KST = ZoneInfo("Asia/Seoul")
                        
                        parsed_time = await ChatService.parse_time_string(time, f"{date} {time}")
                        if parsed_time:
                            proposal_data["start_time"] = parsed_time['start_time'].isoformat()
                            proposal_data["end_time"] = parsed_time['end_time'].isoformat()
                            proposal_data["date"] = parsed_time['start_time'].strftime("%Y년 %m월 %d일")
                    except Exception as e:
                        logger.warning(f"시간 파싱 실패: {str(e)}")
                    
                    # 모든 참여자(요청자 포함)에게 승인 요청 메시지 전송
                    all_participant_ids = [r["user_id"] for r in availability_results]
                    for participant_id in all_participant_ids:
                        await A2AService._send_approval_request_to_chat(
                            user_id=participant_id,
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
                            reject_text = f"저(본인)에게 해당 시간에 {len(conflicts)}개의 일정이 있어 불가능합니다."
                            # A2A 메시지 (내 비서가 나에게/상대에게 알림)
                            for session_info in sessions:
                                await A2ARepository.add_message(
                                    session_id=session_info["session_id"],
                                    sender_user_id=initiator_user_id,
                                    receiver_user_id=session_info["target_id"],
                                    message_type="agent_reply",
                                    message={"text": reject_text, "step": 3}
                                )
                        else:
                            # 상대방(target)이 안 되는 경우 -> 상대방 봇이 말해야 함
                            reject_text = f"{target_name}님이 해당 시간에 일정이 있어 재조율이 필요합니다. ({len(conflicts)}개 일정 충돌)"
                            reco_text = "다른 시간을 입력해주시면 재조율하겠습니다."

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
                                    message={"text": reject_text, "step": 3}
                                )
                                messages.append({
                                    "session_id": target_session["session_id"],
                                    "sender": f"{target_name}봇",
                                    "text": reject_text
                                })

                                # 재조율 요청 멘트
                                await A2ARepository.add_message(
                                    session_id=target_session["session_id"],
                                    sender_user_id=target_id,     # [수정] 보내는 사람: 상대방
                                    receiver_user_id=initiator_user_id,
                                    message_type="proposal", # proposal 타입으로 변경하여 강조
                                    message={"text": reco_text, "step": 4}
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
        일정 승인/거절 처리 (로직 보강)
        1. 승인 상태 확인 방식을 '리스트 신뢰'에서 '개별 로그 전수 조사'로 변경하여 동기화 오류 방지
        2. 캘린더 등록 실패 시(상대방 토큰 만료 등) 에러를 무시하지 않고 결과 메시지에 포함
        """
        try:
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
            for session in sessions:
                # initiator와 target이 동일한 경우(테스트 등)도 고려하여 set으로 처리
                if session.get("initiator_user_id"): all_participants.add(session.get("initiator_user_id"))
                if session.get("target_user_id"): all_participants.add(session.get("target_user_id"))
            
            user = await AuthRepository.find_user_by_id(user_id)
            user_name = user.get("name", "사용자") if user else "사용자"

            # [중요] 참여자가 1명뿐인 경우 (자기 자신과의 채팅 등) 즉시 완료 처리 방지를 위한 로직
            # 실제 배포 환경에서는 최소 2명이어야 의미가 있으나, 테스트 환경을 고려해 로직 유지하되 로그 남김
            if len(all_participants) < 2:
                logger.warning(f"참여자가 1명뿐입니다. 즉시 승인될 수 있습니다. Participants: {all_participants}")

            if approved:
                # 2. [수정됨] 승인 현황 재계산 (Source of Truth: 개별 유저의 최신 로그)
                # 기존 approved_by_list에 의존하지 않고, 각 참여자의 최신 로그를 직접 조회하여 승인 여부 판단
                
                real_approved_users = set()
                
                # 현재 요청한 유저는 승인한 것으로 간주
                real_approved_users.add(user_id)
                
                # 다른 참여자들의 승인 상태 확인
                for pid in all_participants:
                    if pid == user_id: continue # 이미 추가함

                    # 해당 유저의 가장 최근 'schedule_approval' 로그 조회
                    # 주의: thread_id나 session_ids 조건도 맞아야 함
                    query = supabase.table('chat_log').select('*').eq(
                        'user_id', pid
                    ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1)
                    
                    res = query.execute()
                    if res.data:
                        log_meta = res.data[0].get('metadata', {})
                        # 해당 로그의 approved_by가 본인 ID라면 승인한 것으로 판단
                        if log_meta.get('approved_by') == pid:
                            real_approved_users.add(pid)
                
                # 전원 승인 여부 판단
                all_approved = len(real_approved_users) >= len(all_participants)
                approved_list = list(real_approved_users)

                logger.info(f"승인 현황(재계산): {len(real_approved_users)}/{len(all_participants)} - {real_approved_users}")

                # 3. 모든 참여자의 Chat Log 메타데이터 동기화 (UI 업데이트용)
                for participant_id in all_participants:
                    # 각 참여자의 로그 찾기
                    log_query = supabase.table('chat_log').select('*').eq(
                        'user_id', participant_id
                    ).eq('message_type', 'schedule_approval').order('created_at', desc=True).limit(1).execute()
                    
                    if log_query.data:
                        target_log = log_query.data[0]
                        meta = target_log.get('metadata', {})
                        
                        # 업데이트할 메타데이터 구성
                        # approved_by 필드는 "그 유저가 승인했는지"를 나타내므로, 
                        # 현재 participant_id가 이번 요청자(user_id)라면 user_id로 업데이트, 아니면 기존 값 유지
                        new_approved_by = user_id if participant_id == user_id else meta.get('approved_by')
                        
                        new_meta = {
                            **meta,
                            "approved_by_list": approved_list, # 최신 리스트 전파
                            "all_approved": all_approved,
                            "approved_by": new_approved_by,
                            "approved_at": dt_datetime.now().isoformat() if participant_id == user_id else meta.get('approved_at')
                        }
                        
                        supabase.table('chat_log').update({'metadata': new_meta}).eq('id', target_log['id']).execute()

                # 승인 알림 메시지 (채팅방)
                approval_msg_text = f"{user_name}님이 일정을 승인했습니다."
                if all_approved:
                    approval_msg_text += " (전원 승인 완료 - 캘린더 등록 중...)"
                else:
                    remaining = len(all_participants) - len(real_approved_users)
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

                    # 모든 참여자 루프
                    for pid in all_participants:
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
                            evt_summary = f"{proposal.get('participants', ['미팅'])[0]} 등과 미팅" 
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
                # 거절 로직 (기존 코드 유지)
                # ... (필요 시 거절 처리 코드도 동일한 동기화 방식 적용 권장)
                
                # 간단한 거절 처리 예시
                reject_msg = f"{user_name}님이 일정을 거절했습니다. 재조율을 진행합니다."
                for session in sessions:
                     await A2ARepository.add_message(
                        session_id=session["id"],
                        sender_user_id=user_id,
                        receiver_user_id=session.get("target_user_id") if session.get("target_user_id") != user_id else session.get("initiator_user_id"),
                        message_type="schedule_rejection",
                        message={"text": reject_msg}
                    )
                from src.chat.chat_repository import ChatRepository
                
                for pid in all_participants:
                    # 거절한 본인에게는 "거절 처리되었습니다"라고 보내거나 생략 가능
                    # 여기서는 다른 사람들에게 알리는 것이 중요함
                    if pid == user_id:
                        # 먼저 거절 확인 메시지
                        await ChatRepository.create_chat_log(
                            user_id=pid,
                            request_text=None,
                            response_text=f"일정을 거절했습니다.",
                            message_type="system"
                        )
                        # [핵심] 재조율 유도 질문
                        await ChatRepository.create_chat_log(
                            user_id=pid,
                            request_text=None,
                            response_text="재조율을 위해 원하시는 날짜와 시간을 말씀해 주세요.\n(예: 내일 오후 5시)",
                            message_type="ai_response", # 일반 AI 답변처럼 보이게
                            metadata={
                                "needs_recoordination": True,
                                "thread_id": thread_id,
                                "session_ids": session_ids
                            }
                        )
                        continue

                    # 상대방(initiator 등)에게 알림 전송
                    await ChatRepository.create_chat_log(
                        user_id=pid,
                        request_text=None,
                        response_text=f"{reject_msg}\n상대방이 새로운 시간을 입력하면 다시 알려드리겠습니다.",
                        friend_id=None,
                        message_type="schedule_rejection", # 이 타입으로 보내야 함
                        metadata={
                            "needs_recoordination": True, # 재조율 플래그 ON
                            "rejected_by": user_id,
                            "thread_id": thread_id,
                            "session_ids": session_ids
                        }
                    )
                    
                return {"status": 200, "message": "일정을 거절했습니다."}

        except Exception as e:
            logger.error(f"승인 핸들러 오류: {str(e)}", exc_info=True)
            return {"status": 500, "error": str(e)}


    @staticmethod
    async def _send_approval_request_to_chat(
        user_id: str,
        thread_id: Optional[str],
        session_ids: List[str],
        proposal: Dict[str, Any],
        initiator_name: str
    ):
        """
        상대방의 Chat 화면에 승인 요청 메시지 전송
        """
        try:
            from src.chat.chat_repository import ChatRepository
            from src.auth.auth_repository import AuthRepository
            
            date_str = proposal.get("date", "")
            time_str = proposal.get("time", "")
            location_str = proposal.get("location", "")
            
            # 승인 요청을 받는 사용자의 이름 조회
            user_info = await AuthRepository.find_user_by_id(user_id)
            user_name = user_info.get("name", "사용자") if user_info else "사용자"
            
            # 참여자 목록에서 자신을 제외한 다른 참여자들만 표시
            all_participants = proposal.get("participants", [])
            other_participants = [p for p in all_participants if p != user_name]
            
            # 다른 참여자가 없으면 initiator_name 사용 (1:1 일정인 경우)
            if not other_participants:
                other_participants = [initiator_name] if initiator_name else all_participants
            
            participants_str = ", ".join(other_participants) if other_participants else "상대방"
            
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
            logger.error(f"승인 요청 메시지 전송 실패: {str(e)}", exc_info=True)
