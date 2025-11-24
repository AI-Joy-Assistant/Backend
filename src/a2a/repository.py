from typing import List, Dict, Any, Optional
from config.database import supabase
import uuid
from datetime import datetime

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
    async def update_session_status(session_id: str, status: str) -> bool:
        """세션 상태 업데이트"""
        try:
            response = supabase.table('a2a_session').update({
                "status": status,
                "updated_at": datetime.utcnow().isoformat()
            }).eq('id', session_id).execute()
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



