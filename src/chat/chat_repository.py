from typing import List, Dict, Any, Optional
from config.database import supabase
import uuid
import logging

logger = logging.getLogger(__name__)


class ChatRepository:
    # ------------------------------------
    # 1) 기본 채팅/친구 관련 메서드
    # ------------------------------------
    @staticmethod
    async def get_chat_messages(user_id: str, other_user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """두 사용자 간의 채팅 메시지 조회 (chat_log 사용)"""
        try:
            response = (
                supabase
                .table('chat_log')
                .select('*')
                .eq('user_id', user_id)
                .eq('friend_id', other_user_id)
                .order('created_at', desc=False)
                .limit(limit)
                .execute()
            )
            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"채팅 메시지 조회 오류: {str(e)}")

    @staticmethod
    async def send_message(send_id: str, receive_id: str, message: str, message_type: str = "text") -> Dict[str, Any]:
        """메시지 전송 (chat_log 사용)"""
        try:
            message_data = {
                "user_id": send_id,
                "friend_id": receive_id,
                "request_text": message,
                "message_type": message_type,
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

            response = (
                supabase
                .table('user')
                .select('id, name')
                .in_('id', user_ids)
                .execute()
            )

            user_names: Dict[str, str] = {}
            if response.data:
                for user in response.data:
                    user_names[user['id']] = user.get('name', '이름 없음')

            return user_names
        except Exception as e:
            raise Exception(f"사용자 이름 조회 오류: {str(e)}")

    @staticmethod
    async def get_user_details_by_ids(user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """사용자 ID들로 상세 정보(이름, 프로필 이미지) 조회"""
        try:
            if not user_ids:
                return {}

            response = (
                supabase
                .table('user')
                .select('id, name, profile_image')
                .in_('id', user_ids)
                .execute()
            )

            user_details: Dict[str, Dict[str, Any]] = {}
            if response.data:
                for user in response.data:
                    user_details[user['id']] = {
                        "name": user.get('name', '이름 없음'),
                        "profile_image": user.get('profile_image'),
                    }

            return user_details
        except Exception as e:
            raise Exception(f"사용자 상세 정보 조회 오류: {str(e)}")

    @staticmethod
    async def get_friends_list(user_id: str) -> List[Dict[str, Any]]:
        """친구 목록 조회"""
        try:
            response = (
                supabase
                .table('friend_list')
                .select('friend_id')
                .eq('user_id', user_id)
                .eq('status', True)
                .execute()
            )

            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"친구 목록 조회 오류: {str(e)}")

    @staticmethod
    async def create_chat_log(
        user_id: str,
        request_text: Optional[str] = None,
        response_text: Optional[str] = None,
        friend_id: Optional[str] = None,
        message_type: str = "user_message",
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        chat_log 테이블에 한 줄 저장
        - user_id: 필수
        - request_text / response_text 둘 중 하나만 채워도 됨
        - session_id: 새 채팅 세션 uuid (없으면 None)
        - metadata: JSONB 컬럼
        """
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "request_text": request_text,
            "response_text": response_text,
            "friend_id": friend_id,
            "message_type": message_type,
        }

        # session_id 컬럼이 uuid 타입이라, uuid 형식일 때만 넣어주기
        if session_id:
            try:
                uuid.UUID(str(session_id))
            except ValueError:
                logger.warning(
                    f"create_chat_log: 잘못된 session_id 형식, 저장하지 않음: {session_id}"
                )
            else:
                payload["session_id"] = str(session_id)

        if metadata is not None:
            payload["metadata"] = metadata

        res = supabase.table("chat_log").insert(payload).execute()
        if not res.data:
            raise Exception("chat_log insert 실패")

        return res.data[0]

    @staticmethod
    async def get_chat_logs_by_user(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """사용자의 AI 채팅 로그 조회"""
        try:
            response = (
                supabase
                .table('chat_log')
                .select('*')
                .eq('user_id', user_id)
                .order('created_at', desc=True)
                .limit(limit)
                .execute()
            )

            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"채팅 로그 조회 오류: {str(e)}")

    @staticmethod
    async def get_user_chat_sessions(user_id: str) -> List[Dict[str, Any]]:
        """사용자의 친구별 채팅 목록 조회"""
        try:
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
            response = (
                supabase
                .table('chat_log')
                .select('*')
                .eq('user_id', user_id)
                .eq('friend_id', friend_id)
                .order('created_at', desc=False)
                .execute()
            )

            return response.data if response.data else []
        except Exception as e:
            raise Exception(f"친구 메시지 조회 오류: {str(e)}")

    @staticmethod
    async def delete_user_friend_session(user_id: str, friend_id: str) -> int:
        """사용자-친구 간 세션(chat_log) 전체 삭제, 삭제된 행 수 반환"""
        try:
            response = (
                supabase
                .table('chat_log')
                .delete()
                .eq('user_id', user_id)
                .eq('friend_id', friend_id)
                .execute()
            )
            deleted_count = len(response.data) if response.data else 0
            return deleted_count
        except Exception as e:
            raise Exception(f"세션 삭제 오류: {str(e)}")

    # ------------------------------------
    # 2) 세션 기반 메서드
    # ------------------------------------
    @staticmethod
    async def get_recent_chat_logs(
        user_id: str,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        최근 채팅 로그 조회
        - session_id가 있으면 해당 세션만
        - 없으면 유저 전체 기준 최근 로그
        """
        try:
            query = (
                supabase.table("chat_log")
                .select("*")
                .eq("user_id", user_id)
            )

            if session_id:
                # uuid 형식일 때만 필터 적용
                try:
                    uuid.UUID(str(session_id))
                except ValueError:
                    logger.warning(
                        f"잘못된 session_id 형식: {session_id} (uuid 아님, session 필터 스킵)"
                    )
                else:
                    query = query.eq("session_id", str(session_id))

            res = query.order("created_at", desc=True).limit(limit).execute()
            return res.data or []
        except Exception as e:
            raise Exception(f"최근 채팅 로그 조회 오류: {str(e)}")

    @staticmethod
    async def delete_chat_room(user_id: str, friend_id: str) -> int:
        """특정 친구와의 채팅 로그 전체 삭제(현재 사용자 관점)"""
        try:
            response = (
                supabase
                .table('chat_log')
                .delete()
                .eq('user_id', user_id)
                .eq('friend_id', friend_id)
                .execute()
            )
            return len(response.data) if response.data else 0
        except Exception as e:
            raise Exception(f"채팅방 삭제 오류: {str(e)}")

    @staticmethod
    async def get_chat_logs_by_session(
        user_id: str,
        session_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """특정 세션의 채팅 로그만 조회"""
        try:
            res = (
                supabase
                .table("chat_log")
                .select("*")
                .eq("user_id", user_id)
                .eq("session_id", session_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception as e:
            raise Exception(f"세션별 채팅 로그 조회 오류: {str(e)}")
