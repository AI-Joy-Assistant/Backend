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
        import time
        start_time = time.time()
        
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
            
            elapsed = time.time() - start_time
            # logger.info(f"⏱️ get_recent_chat_logs 쿼리 시간: {elapsed:.3f}초 (rows: {len(res.data or [])})")
            
            return res.data or []
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"⏱️ get_recent_chat_logs 오류 발생 (시간: {elapsed:.3f}초): {str(e)}")
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

    @staticmethod
    async def delete_all_user_data(user_id: str) -> None:
        """사용자와 관련된 모든 채팅 데이터 삭제 (탈퇴용)"""
        try:
            # 사용자가 user_id인 경우 OR friend_id인 경우 모두 삭제
            # Supabase PostgREST for OR: or=(user_id.eq.X,friend_id.eq.X)
            print(f"🗑️ [Chat] 사용자 관련 모든 채팅 삭제 시작: {user_id}")
            
            response = (
                supabase
                .table('chat_log')
                .delete()
                .or_(f"user_id.eq.{user_id},friend_id.eq.{user_id}")
                .execute()
            )
            
            deleted_count = len(response.data) if response.data else 0
            print(f"✅ [Chat] 사용자 관련 채팅 삭제 완료: {deleted_count}건")
            
        except Exception as e:
            print(f"❌ [Chat] 데이터 삭제 오류: {str(e)}")
            raise Exception(f"채팅 데이터 삭제 실패: {str(e)}")
    @staticmethod
    async def update_session_title(session_id: str, title: str, user_id: str) -> None:
        """세션 제목 업데이트"""
        try:
            # 세션 확인
            check = supabase.table("chat_sessions").select("id").eq("id", session_id).eq("user_id", user_id).execute()
            if not check.data:
                logger.warning(f"세션 제목 업데이트 중단: 세션이 없거나 권한 없음 (session_id={session_id}, user_id={user_id})")
                return

            supabase.table("chat_sessions").update({
                "title": title
            }).eq("id", session_id).execute()
            # logger.info(f"세션 제목 업데이트 성공: {title} (session_id={session_id})")
        except Exception as e:
            logger.error(f"세션 제목 업데이트 실패: {str(e)}")
            # 에러 발생해도 로직 중단하지 않음

    @staticmethod
    async def get_default_session(user_id: str) -> Optional[Dict[str, Any]]:
        """
        사용자의 기본 채팅 세션 조회 또는 생성
        - chat_sessions 테이블에서 is_default=true인 세션 찾기
        - 없으면 새로 생성
        """
        try:
            # 기본 세션 조회
            response = (
                supabase
                .table('chat_sessions')
                .select('*')
                .eq('user_id', user_id)
                .eq('is_default', True)
                .limit(1)
                .execute()
            )
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            
            # 기본 세션이 없으면 가장 최근 세션 반환
            response = (
                supabase
                .table('chat_sessions')
                .select('*')
                .eq('user_id', user_id)
                .order('created_at', desc=True)
                .limit(1)
                .execute()
            )
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            
            # 세션이 아예 없으면 새로 생성
            new_session_id = str(uuid.uuid4())
            new_session = {
                "id": new_session_id,
                "user_id": user_id,
                "title": "새 채팅",
                "is_default": True
            }
            
            insert_response = supabase.table('chat_sessions').insert(new_session).execute()
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
        """
        채팅 세션에 메시지 추가
        """
        try:
            # 세션 정보 조회
            session_response = (
                supabase
                .table('chat_sessions')
                .select('user_id')
                .eq('id', session_id)
                .limit(1)
                .execute()
            )
            
            if not session_response.data:
                logger.warning(f"메시지 추가 실패: 세션 없음 (session_id={session_id})")
                return None
            
            user_id = session_response.data[0]['user_id']
            
            # chat_log에 메시지 추가
            payload = {
                "user_id": user_id,
                "session_id": session_id,
                "request_text": user_message,
                "response_text": ai_response,
                "message_type": intent
            }
            
            insert_response = supabase.table('chat_log').insert(payload).execute()
            if insert_response.data:
                return insert_response.data[0]
            
            return None
            
        except Exception as e:
            logger.error(f"메시지 추가 오류: {str(e)}")
            return None
