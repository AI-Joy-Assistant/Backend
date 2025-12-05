from typing import List, Dict, Any, Optional
from config.database import supabase
import uuid
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class A2ARepository:
    
    @staticmethod
    async def create_session(
        initiator_user_id: str,
        target_user_id: str,
        intent: str = "schedule",
        time_window: Optional[Dict[str, Any]] = None,
        place_pref: Optional[Dict[str, Any]] = None,
        summary: Optional[str] = None
    ) -> Dict[str, Any]:
        """A2A 세션 생성"""
        try:
            session_id = str(uuid.uuid4())
            # a2a_session 테이블의 실제 컬럼 구조에 맞춰 생성
            # 필수 필드만 포함 (summary, time_window, place_pref는 선택적)
            session_data = {
                "id": session_id,
                "initiator_user_id": initiator_user_id,
                "target_user_id": target_user_id,
                "intent": intent,
                "status": "pending",
            }
            
            # time_window와 place_pref는 JSONB 필드일 수 있으므로 조건부로 추가
            # summary는 place_pref에 포함시키거나 제외
            if place_pref is not None:
                session_data["place_pref"] = place_pref
            elif summary is not None:
                # summary가 있으면 place_pref에 포함
                session_data["place_pref"] = {"summary": summary}
            
            if time_window is not None:
                session_data["time_window"] = time_window
            
            response = supabase.table('a2a_session').insert(session_data).execute()
            if response.data:
                return response.data[0]
            raise Exception("세션 생성 실패")
        except Exception as e:
            raise Exception(f"세션 생성 오류: {str(e)}")
    
    @staticmethod
    async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
        """세션 조회"""
        try:
            response = supabase.table('a2a_session').select('*').eq('id', session_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            raise Exception(f"세션 조회 오류: {str(e)}")
    
    @staticmethod
    async def update_session_status(session_id: str, status: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """세션 상태 업데이트"""
        try:
            update_data = {
                "status": status,
                "updated_at": datetime.utcnow().isoformat()
            }
            # details는 무시 (details 컬럼이 테이블에 없음)
            
            response = supabase.table('a2a_session').update(update_data).eq('id', session_id).execute()
            return len(response.data) > 0
        except Exception as e:
            raise Exception(f"세션 상태 업데이트 오류: {str(e)}")
    
    @staticmethod
    async def add_message(
        session_id: str,
        sender_user_id: str,
        receiver_user_id: str,
        message_type: str,
        message: Dict[str, Any]
    ) -> Dict[str, Any]:
        """A2A 메시지 추가"""
        try:
            message_data = {
                "session_id": session_id,
                "sender_user_id": sender_user_id,
                "receiver_user_id": receiver_user_id,
                "type": message_type,
                "message": message,
            }
            
            response = supabase.table('a2a_message').insert(message_data).execute()
            if response.data:
                return response.data[0]
            raise Exception("메시지 저장 실패")
        except Exception as e:
            raise Exception(f"메시지 저장 오류: {str(e)}")
    
    @staticmethod
    async def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
        """세션의 모든 메시지 조회"""
        try:
            response = supabase.table('a2a_message').select('*').eq(
                'session_id', session_id
            ).order('created_at', desc=False).execute()
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"메시지 조회 오류: {str(e)}")
    
    @staticmethod
    async def get_thread_messages(thread_id: str) -> List[Dict[str, Any]]:
        """thread_id에 속한 모든 세션의 메시지 조회 (단체 채팅방용)"""
        try:
            # thread_id를 가진 모든 세션 찾기 (get_thread_sessions 사용)
            thread_sessions = await A2ARepository.get_thread_sessions(thread_id)
            
            if not thread_sessions:
                return []
            
            session_ids = [s['id'] for s in thread_sessions]
            
            # 모든 세션의 메시지 조회
            all_messages = []
            for sid in session_ids:
                messages = await A2ARepository.get_session_messages(sid)
                all_messages.extend(messages)
            
            # 시간순 정렬
            all_messages.sort(key=lambda x: x.get('created_at', ''))
            
            return all_messages
        except Exception as e:
            raise Exception(f"thread 메시지 조회 오류: {str(e)}")
    
    @staticmethod
    async def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
        """사용자의 모든 세션 조회"""
        try:
            response = supabase.table('a2a_session').select('*').or_(
                f'initiator_user_id.eq.{user_id},target_user_id.eq.{user_id}'
            ).order('created_at', desc=True).execute()
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"세션 목록 조회 오류: {str(e)}")
    
    @staticmethod
    async def get_pending_requests_for_user(user_id: str) -> List[Dict[str, Any]]:
        """
        사용자에게 온 pending 상태의 일정 요청 조회
        - target_user_id가 현재 사용자인 세션
        - status가 'pending', 'pending_approval', 'in_progress'인 세션
        """
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"🔍 Pending 요청 조회 시작 - user_id: {user_id}")
            
            response = supabase.table('a2a_session').select('*').eq(
                'target_user_id', user_id
            ).in_('status', ['pending', 'pending_approval', 'in_progress']).order('created_at', desc=True).execute()
            
            logger.info(f"🔍 Pending 요청 조회 결과: {len(response.data) if response.data else 0}건")
            if response.data:
                for s in response.data:
                    logger.info(f"   - 세션: {s.get('id')}, status: {s.get('status')}, initiator: {s.get('initiator_user_id')}")
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"pending 요청 조회 오류: {str(e)}")
    
    @staticmethod
    async def find_existing_session(
        initiator_user_id: str,
        target_user_ids: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        같은 참여자들로 진행 중이거나 최근에 생성된 기존 세션 찾기
        여러 참여자가 있는 경우, 모든 참여자가 포함된 세션을 찾습니다.
        """
        try:
            # 모든 참여자 ID (initiator + targets)
            all_participants = [initiator_user_id] + target_user_ids
            
            # initiator가 같고, target_user_id가 target_user_ids 중 하나인 세션들 조회
            # 최근 생성된 순으로 정렬하여 가장 최근 세션 반환
            sessions = []
            for target_id in target_user_ids:
                response = supabase.table('a2a_session').select('*').eq(
                    'initiator_user_id', initiator_user_id
                ).eq('target_user_id', target_id).order('created_at', desc=True).limit(1).execute()
                
                if response.data:
                    sessions.extend(response.data)
            
            # 반대 방향도 확인 (target이 initiator였던 경우)
            for target_id in target_user_ids:
                response = supabase.table('a2a_session').select('*').eq(
                    'initiator_user_id', target_id
                ).eq('target_user_id', initiator_user_id).order('created_at', desc=True).limit(1).execute()
                
                if response.data:
                    sessions.extend(response.data)
            
            if not sessions:
                return None
            
            # 가장 최근 세션 반환
            # completed 상태가 아닌 세션 우선, 없으면 가장 최근 세션
            in_progress = [s for s in sessions if s.get('status') in ['pending', 'in_progress']]
            if in_progress:
                # 가장 최근 진행 중인 세션
                return max(in_progress, key=lambda x: x.get('created_at', ''))
            else:
                # 가장 최근 세션 (completed 포함)
                return max(sessions, key=lambda x: x.get('created_at', ''))
                
        except Exception as e:
            logger.warning(f"기존 세션 찾기 오류: {str(e)}")
            return None
    
    @staticmethod
    async def delete_session(session_id: str) -> bool:
        """A2A 세션 삭제 (관련 메시지도 함께 삭제)"""
        try:
            # 먼저 관련 메시지 삭제
            supabase.table('a2a_message').delete().eq('session_id', session_id).execute()
            
            # 세션 삭제
            response = supabase.table('a2a_session').delete().eq('id', session_id).execute()
            
            # 삭제 성공 여부 확인
            return True
        except Exception as e:
            raise Exception(f"세션 삭제 오류: {str(e)}")
    
    @staticmethod
    async def create_thread(
        initiator_id: str,
        participant_ids: List[str],
        title: Optional[str] = None
    ) -> Dict[str, Any]:
        """A2A Thread 생성 (다중 사용자 그룹)"""
        try:
            thread_id = str(uuid.uuid4())
            # 첫 번째 참여자를 counterpart로 설정 (나머지는 place_pref에 저장)
            counterpart_id = participant_ids[0] if participant_ids else initiator_id
            
            thread_data = {
                "id": thread_id,
                "initiator_id": initiator_id,
                "counterpart_id": counterpart_id,
                "title": title or "일정 조율",
                "status": "open"
            }
            
            response = supabase.table('a2a_thread').insert(thread_data).execute()
            if response.data:
                return response.data[0]
            raise Exception("Thread 생성 실패")
        except Exception as e:
            raise Exception(f"Thread 생성 오류: {str(e)}")
    
    @staticmethod
    async def get_thread_sessions(thread_id: str) -> List[Dict[str, Any]]:
        """Thread에 속한 모든 세션 조회"""
        try:
            # place_pref에 thread_id가 포함된 세션들 조회
            # 또는 별도 테이블이 있다면 그걸 사용
            # 일단 간단하게 place_pref에 thread_id를 저장하는 방식 사용
            response = supabase.table('a2a_session').select('*').contains(
                'place_pref', {'thread_id': thread_id}
            ).execute()
            return response.data if response.data else []
        except Exception as e:
            logger.warning(f"Thread 세션 조회 실패: {str(e)}")
            return []
    
    @staticmethod
    async def link_calendar_event(session_id: str, google_event_id: str) -> bool:
        """캘린더 이벤트와 세션 연결 (양방향 연결)"""
        try:
            # 1) calendar_event 테이블에서 google_event_id로 찾아서 session_id 업데이트
            calendar_response = supabase.table('calendar_event').update({
                "session_id": session_id,
                "updated_at": datetime.utcnow().isoformat()
            }).eq('google_event_id', google_event_id).execute()
            
            # 2) a2a_session 테이블의 final_event_id 업데이트
            # calendar_event의 id를 가져와야 하는데, google_event_id로 조회한 결과에서 id 추출
            if calendar_response.data and len(calendar_response.data) > 0:
                calendar_event_id = calendar_response.data[0].get('id')
                if calendar_event_id:
                    session_response = supabase.table('a2a_session').update({
                        "final_event_id": calendar_event_id,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq('id', session_id).execute()
                    return len(session_response.data) > 0
            return len(calendar_response.data) > 0
        except Exception as e:
            raise Exception(f"이벤트 연결 오류: {str(e)}")

    @staticmethod
    async def delete_room(room_id: str) -> bool:
        """
        채팅방 삭제 (Thread ID 또는 Session ID)
        - room_id가 Thread ID라면: 해당 스레드에 속한 모든 세션 삭제
        - room_id가 Session ID라면: 해당 세션 삭제
        """
        try:
            session_ids_to_delete = set()

            # 1. room_id가 세션 ID인 경우 조회
            res_session = supabase.table('a2a_session').select('id').eq('id', room_id).execute()
            if res_session.data:
                for s in res_session.data:
                    session_ids_to_delete.add(s['id'])

            # 2. room_id가 스레드 ID인 경우 조회 (place_pref에 thread_id가 포함된 세션)
            # contains 연산자를 사용하여 JSONB 필드 검색
            res_thread = supabase.table('a2a_session').select('id').contains('place_pref', {'thread_id': room_id}).execute()
            if res_thread.data:
                for s in res_thread.data:
                    session_ids_to_delete.add(s['id'])

            ids_list = list(session_ids_to_delete)

            if ids_list:
                logger.info(f"삭제할 세션 ID 목록: {ids_list}")

                # 3. 종속 데이터 삭제 (순서 중요)

                # 3-1) a2a_message 삭제
                supabase.table('a2a_message').delete().in_('session_id', ids_list).execute()

                # 3-2) calendar_event 연결 해제 (삭제 대신 NULL 처리)
                # session_id 컬럼이 nullable이어야 오류가 나지 않습니다.
                supabase.table('calendar_event').update({'session_id': None}).in_('session_id', ids_list).execute()

                # 3-3) a2a_session 삭제
                supabase.table('a2a_session').delete().in_('id', ids_list).execute()

            # 4. a2a_thread 삭제 (존재한다면)
            supabase.table('a2a_thread').delete().eq('id', room_id).execute()

            return True

        except Exception as e:
            logger.error(f"방 삭제 오류: {str(e)}")
            return False


