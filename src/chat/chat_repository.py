from typing import List, Dict, Any, Optional
from config.database import get_async_supabase, supabase
import uuid
import logging

logger = logging.getLogger(__name__)


class ChatRepository:
    """채팅 관련 데이터베이스 작업 - Async 버전"""
    
    @staticmethod
    async def _get_client():
        """비동기 Supabase 클라이언트 반환"""
        return await get_async_supabase()
    
    # ------------------------------------
    # 1) 기본 채팅/친구 관련 메서드
    # ------------------------------------
    @staticmethod
    async def get_chat_messages(user_id: str, other_user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """두 사용자 간의 채팅 메시지 조회"""
        try:
            client = await ChatRepository._get_client()
            response = await (
                client
                .table('chat_log')
                .select('id, user_id, friend_id, request_text, response_text, message_type, created_at')
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
        """메시지 전송"""
        try:
            client = await ChatRepository._get_client()
            message_data = {
                "user_id": send_id,
                "friend_id": receive_id,
                "request_text": message,
                "message_type": message_type,
            }
            response = await client.table('chat_log').insert(message_data).execute()
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

            # [FIX] 가상 사용자 ID 필터링 (tutorial_guide_joyner 등 UUID가 아닌 ID 제외)
            import uuid
            def is_valid_uuid(val):
                try:
                    uuid.UUID(str(val))
                    return True
                except ValueError:
                    return False
            
            valid_ids = [uid for uid in user_ids if is_valid_uuid(uid)]
            
            if not valid_ids:
                return {}

            client = await ChatRepository._get_client()
            response = await (
                client

                .table('user')
                .select('id, name')
                .in_('id', valid_ids)
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
        """사용자 ID들로 상세 정보 조회"""
        try:
            if not user_ids:
                return {}
            client = await ChatRepository._get_client()
            response = await (
                client
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
            client = await ChatRepository._get_client()
            response = await (
                client
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
        """chat_log 테이블에 한 줄 저장"""
        # [FIX] friend_id가 유효한 UUID이고 user 테이블에 존재하는지 확인
        validated_friend_id = None
        if friend_id:
            try:
                uuid.UUID(str(friend_id))
                # user 테이블에서 존재 여부 확인
                client = await ChatRepository._get_client()
                user_check = await client.table("user").select("id").eq("id", friend_id).limit(1).execute()
                if user_check.data and len(user_check.data) > 0:
                    validated_friend_id = friend_id
                else:
                    logger.warning(f"create_chat_log: friend_id '{friend_id}' 가 user 테이블에 없음 → None 처리")
            except ValueError:
                logger.warning(f"create_chat_log: 잘못된 friend_id 형식: {friend_id}")
        
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "request_text": request_text,
            "response_text": response_text,
            "friend_id": validated_friend_id,
            "message_type": message_type,
        }

        if session_id:
            try:
                uuid.UUID(str(session_id))
                payload["session_id"] = str(session_id)
            except ValueError:
                logger.warning(f"create_chat_log: 잘못된 session_id 형식: {session_id}")

        if metadata is not None:
            payload["metadata"] = metadata

        client = await ChatRepository._get_client()
        res = await client.table("chat_log").insert(payload).execute()
        if not res.data:
            raise Exception("chat_log insert 실패")
        return res.data[0]

    @staticmethod
    async def get_chat_logs_by_user(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """사용자의 AI 채팅 로그 조회"""
        try:
            client = await ChatRepository._get_client()
            response = await (
                client
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
        """사용자의 친구별 채팅 목록 조회 - 최적화됨"""
        try:
            client = await ChatRepository._get_client()
            response = await (
                client
                .table('chat_log')
                .select('friend_id, request_text, response_text, created_at')
                .eq('user_id', user_id)
                .not_.is_('friend_id', 'null')
                .order('created_at', desc=True)
                .limit(100)
                .execute()
            )
            
            if not response.data:
                return []
            
            seen_friends = set()
            latest_per_friend = []
            for row in response.data:
                fid = row['friend_id']
                if fid not in seen_friends:
                    seen_friends.add(fid)
                    latest_per_friend.append(row)
            
            return latest_per_friend
        except Exception as e:
            raise Exception(f"채팅 세션 조회 오류: {str(e)}")

    @staticmethod
    async def get_friend_messages(user_id: str, friend_id: str) -> List[Dict[str, Any]]:
        """특정 친구와의 모든 메시지 조회"""
        try:
            client = await ChatRepository._get_client()
            response = await (
                client
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
        """사용자-친구 간 세션 전체 삭제"""
        try:
            client = await ChatRepository._get_client()
            response = await (
                client
                .table('chat_log')
                .delete()
                .eq('user_id', user_id)
                .eq('friend_id', friend_id)
                .execute()
            )
            return len(response.data) if response.data else 0
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
        """최근 채팅 로그 조회"""
        import time
        start_time = time.time()
        
        try:
            client = await ChatRepository._get_client()
            query = (
                client.table("chat_log")
                .select("*")
                .eq("user_id", user_id)
            )

            if session_id:
                try:
                    uuid.UUID(str(session_id))
                    query = query.eq("session_id", str(session_id))
                except ValueError:
                    logger.warning(f"잘못된 session_id 형식: {session_id}")

            res = await query.order("created_at", desc=True).limit(limit).execute()
            
            elapsed = time.time() - start_time
            logger.info(f"⏱️ get_recent_chat_logs 쿼리 시간: {elapsed:.3f}초 (rows: {len(res.data or [])})")
            
            return res.data or []
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"⏱️ get_recent_chat_logs 오류 (시간: {elapsed:.3f}초): {str(e)}")
            raise Exception(f"최근 채팅 로그 조회 오류: {str(e)}")

    @staticmethod
    async def delete_chat_room(user_id: str, friend_id: str) -> int:
        """특정 친구와의 채팅 로그 전체 삭제"""
        try:
            client = await ChatRepository._get_client()
            response = await (
                client
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
            client = await ChatRepository._get_client()
            res = await (
                client
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

    @staticmethod
    async def delete_all_user_data(user_id: str) -> None:
        """사용자와 관련된 모든 채팅 데이터 삭제 (탈퇴용)"""
        try:
            client = await ChatRepository._get_client()
            print(f"🗑️ [Chat] 사용자 관련 모든 채팅 삭제 시작: {user_id}")
            
            # 1. chat_log 삭제
            response1 = await (
                client
                .table('chat_log')
                .delete()
                .or_(f"user_id.eq.{user_id},friend_id.eq.{user_id}")
                .execute()
            )
            deleted_logs = len(response1.data) if response1.data else 0
            print(f"✅ [Chat] chat_log 삭제 완료: {deleted_logs}건")
            
            # 2. chat_sessions 삭제 (FK 제약으로 인해 user 삭제 전 필수)
            response2 = await (
                client
                .table('chat_sessions')
                .delete()
                .eq('user_id', user_id)
                .execute()
            )
            deleted_sessions = len(response2.data) if response2.data else 0
            print(f"✅ [Chat] chat_sessions 삭제 완료: {deleted_sessions}건")
            
        except Exception as e:
            print(f"❌ [Chat] 데이터 삭제 오류: {str(e)}")
            raise Exception(f"채팅 데이터 삭제 실패: {str(e)}")

    @staticmethod
    async def update_session_title(session_id: str, title: str, user_id: str) -> None:
        """세션 제목 업데이트"""
        try:
            client = await ChatRepository._get_client()
            check = await client.table("chat_sessions").select("id").eq("id", session_id).eq("user_id", user_id).limit(1).execute()
            if not check.data:
                logger.warning(f"세션 제목 업데이트 중단: 세션이 없거나 권한 없음")
                return

            await client.table("chat_sessions").update({"title": title}).eq("id", session_id).execute()
            logger.info(f"세션 제목 업데이트 성공: {title}")
        except Exception as e:
            logger.error(f"세션 제목 업데이트 실패: {str(e)}")

    @staticmethod
    async def get_default_session(user_id: str) -> Optional[Dict[str, Any]]:
        """사용자의 기본 채팅 세션 조회 또는 생성"""
        try:
            client = await ChatRepository._get_client()
            
            response = await (
                client
                .table('chat_sessions')
                .select('*')
                .eq('user_id', user_id)
                .eq('is_default', True)
                .limit(1)
                .execute()
            )
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            
            response = await (
                client
                .table('chat_sessions')
                .select('*')
                .eq('user_id', user_id)
                .order('created_at', desc=True)
                .limit(1)
                .execute()
            )
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            
            new_session_id = str(uuid.uuid4())
            new_session = {
                "id": new_session_id,
                "user_id": user_id,
                "title": "새 채팅",
                "is_default": True
            }
            
            insert_response = await client.table('chat_sessions').insert(new_session).execute()
            if insert_response.data:
                return insert_response.data[0]
            
            return {"id": new_session_id, "user_id": user_id}
            
        except Exception as e:
            logger.error(f"기본 세션 조회/생성 오류: {str(e)}")
            return None

    @staticmethod
    async def add_message(
        session_id: str,
        user_message: Optional[str],
        ai_response: Optional[str],
        intent: str = "general"
    ) -> Optional[Dict[str, Any]]:
        """채팅 세션에 메시지 추가"""
        try:
            client = await ChatRepository._get_client()
            
            session_response = await (
                client
                .table('chat_sessions')
                .select('user_id')
                .eq('id', session_id)
                .limit(1)
                .execute()
            )
            
            if not session_response.data:
                logger.warning(f"메시지 추가 실패: 세션 없음")
                return None
            
            user_id = session_response.data[0]['user_id']
            
            payload = {
                "user_id": user_id,
                "session_id": session_id,
                "request_text": user_message,
                "response_text": ai_response,
                "message_type": intent
            }
            
            insert_response = await client.table('chat_log').insert(payload).execute()
            if insert_response.data:
                return insert_response.data[0]
            
            return None
            
        except Exception as e:
            logger.error(f"메시지 추가 오류: {str(e)}")
            return None
