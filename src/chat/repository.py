from typing import List, Dict, Any, Optional
from config.database import supabase
import uuid

class ChatRepository:
    
    @staticmethod
    async def get_chat_messages(user_id: str, other_user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """두 사용자 간의 채팅 메시지 조회 (chat_log 사용)"""
        try:
            # chat_log에서 두 사용자 간의 메시지 조회
            response = supabase.table('chat_log').select('*').eq(
                'user_id', user_id
            ).eq('friend_id', other_user_id).order('created_at', desc=False).limit(limit).execute()
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"채팅 메시지 조회 오류: {str(e)}")
    
    @staticmethod
    async def send_message(send_id: str, receive_id: str, message: str, message_type: str = "text") -> Dict[str, Any]:
        """메시지 전송 (chat_log 사용)"""
        try:
            # chat_log에 사용자 메시지 저장
            message_data = {
                "user_id": send_id,
                "friend_id": receive_id,
                "request_text": message,
                "message_type": message_type
            }
            
            response = supabase.table('chat_log').insert(message_data).execute()
            if response.data:
                return response.data[0]
            raise Exception("메시지 전송 실패")
        except Exception as e:
            raise Exception(f"메시지 전송 오류: {str(e)}")
    
    @staticmethod
    async def get_user_names_by_ids(user_ids: List[str]) -> Dict[str, str]:
        """사용자 ID들로 이름 조회"""
        try:
            if not user_ids:
                return {}
            
            response = supabase.table('user').select('id, name').in_('id', user_ids).execute()
            
            # {user_id: name} 형태의 딕셔너리로 변환
            user_names = {}
            if response.data:
                for user in response.data:
                    user_names[user['id']] = user.get('name', '이름 없음')
            
            return user_names
        except Exception as e:
            raise Exception(f"사용자 이름 조회 오류: {str(e)}")
    
    @staticmethod
    async def get_friends_list(user_id: str) -> List[Dict[str, Any]]:
        """친구 목록 조회"""
        try:
            response = supabase.table('friend_list').select(
                'friend_id'
            ).eq('user_id', user_id).eq('status', True).execute()
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"친구 목록 조회 오류: {str(e)}")
    
    @staticmethod  
    async def create_chat_log(
        user_id: str, 
        request_text: str = None, 
        response_text: str = None,
        friend_id: str = None,
        message_type: str = "user_request"
    ) -> Dict[str, Any]:
        """AI 채팅 로그 생성"""
        try:
            log_data = {
                "user_id": user_id,
                "request_text": request_text,
                "response_text": response_text,
                "friend_id": friend_id,
                "message_type": message_type
            }
            
            # None 값들 제거
            log_data = {k: v for k, v in log_data.items() if v is not None}
            
            response = supabase.table('chat_log').insert(log_data).execute()
            if response.data:
                return response.data[0]
            raise Exception("채팅 로그 생성 실패")
        except Exception as e:
            raise Exception(f"채팅 로그 생성 오류: {str(e)}")
    
    @staticmethod
    async def get_chat_logs_by_user(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """사용자의 AI 채팅 로그 조회"""
        try:
            response = supabase.table('chat_log').select('*').eq(
                'user_id', user_id
            ).order('created_at', desc=True).limit(limit).execute()
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"채팅 로그 조회 오류: {str(e)}")
    
    @staticmethod
    async def get_user_chat_sessions(user_id: str) -> List[Dict[str, Any]]:
        """사용자의 친구별 채팅 목록 조회"""
        try:
            # 일부 환경에서 is_('not.null')가 400을 유발하는 케이스가 있어 not_.is_('null')로 대체
            response = (
                supabase
                .table('chat_log')
                .select('friend_id, request_text, response_text, created_at')
                .eq('user_id', user_id)
                .not_.is_('friend_id', 'null')
                .order('created_at', desc=True)
                .execute()
            )
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"채팅 세션 조회 오류: {str(e)}")
    
    @staticmethod
    async def get_friend_messages(user_id: str, friend_id: str) -> List[Dict[str, Any]]:
        """특정 친구와의 모든 메시지 조회"""
        try:
            response = supabase.table('chat_log').select('*').eq(
                'user_id', user_id
            ).eq('friend_id', friend_id).order('created_at', desc=False).execute()
            
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"친구 메시지 조회 오류: {str(e)}")

    @staticmethod
    async def delete_user_friend_session(user_id: str, friend_id: str) -> int:
        """사용자-친구 간 세션(chat_log) 전체 삭제, 삭제된 행 수 반환"""
        try:
            response = supabase.table('chat_log').delete().eq('user_id', user_id).eq('friend_id', friend_id).execute()
            # supabase-py는 반환 데이터에 삭제된 행이 포함될 수 있음
            deleted_count = len(response.data) if response.data else 0
            return deleted_count
        except Exception as e:
            raise Exception(f"세션 삭제 오류: {str(e)}")
    
    @staticmethod
    async def get_recent_chat_logs(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """사용자의 최근 AI 채팅 로그 조회 (대화 히스토리용)"""
        try:
            response = supabase.table('chat_log').select('*').eq(
                'user_id', user_id
            ).order('created_at', desc=True).limit(limit).execute()
            
            # 시간순으로 정렬 (오래된 것부터)
            if response.data:
                return sorted(response.data, key=lambda x: x['created_at'])
            return []
        except Exception as e:
            raise Exception(f"최근 채팅 로그 조회 오류: {str(e)}") 

    @staticmethod
    async def delete_chat_room(user_id: str, friend_id: str) -> int:
        """특정 친구와의 채팅 로그 전체 삭제(현재 사용자 관점)"""
        try:
            (
                supabase
                .table('chat_log')
                .delete()
                .eq('user_id', user_id)
                .eq('friend_id', friend_id)
                .execute()
            )
            # Supabase는 delete 기본동작이 representation 미반환이므로 성공 시 1로 간주
            return 1
        except Exception as e:
            raise Exception(f"채팅방 삭제 오류: {str(e)}")