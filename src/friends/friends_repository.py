from typing import List, Dict, Any, Optional
from config.database import get_async_supabase
from config.settings import settings
from datetime import datetime
import uuid

class FriendsRepository:
    """친구 관련 데이터베이스 작업 - Async 버전"""
    
    async def _get_client(self):
        """비동기 Supabase 클라이언트 반환"""
        return await get_async_supabase()
    
    async def get_user_by_email_or_handle(self, identifier: str) -> Optional[Dict[str, Any]]:
        """이메일 또는 handle로 사용자 조회"""
        try:
            client = await self._get_client()
            
            # 먼저 이메일로 검색
            response = await client.table('user').select('id, name, email, profile_image, handle').eq('email', identifier).limit(1).execute()
            if response.data:
                return response.data[0]
            
            # 이메일로 찾지 못하면 handle로 검색
            response = await client.table('user').select('id, name, email, profile_image, handle').eq('handle', identifier).limit(1).execute()
            if response.data:
                return response.data[0]
            
            return None
        except Exception as e:
            print(f"사용자 조회 오류: {e}")
            return None
    
    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """사용자 ID로 조회"""
        try:
            client = await self._get_client()
            response = await client.table('user').select('id, name, email, profile_image').eq('id', user_id).limit(1).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"사용자 조회 오류: {e}")
            return None
    
    async def create_friend_request(self, from_user_id: str, to_user_id: str) -> Dict[str, Any]:
        """친구 요청 생성"""
        try:
            client = await self._get_client()
            
            # 이미 친구 요청이 있는지 확인
            existing_request = await client.table('friend_follow').select('id').eq('request_id', from_user_id).eq('receiver_id', to_user_id).eq('follow_status', 'pending').limit(1).execute()
            
            if existing_request.data:
                return {"success": False, "message": "이미 친구 요청을 보냈습니다."}
            
            # 이미 친구인지 확인
            existing_friend = await client.table('friend_list').select('id').eq('user_id', from_user_id).eq('friend_id', to_user_id).eq('status', True).limit(1).execute()
            
            if existing_friend.data:
                return {"success": False, "message": "이미 친구입니다."}
            
            request_data = {
                "request_id": from_user_id,
                "receiver_id": to_user_id,
                "follow_status": "pending",
                "requested_at": datetime.now().isoformat()
            }
            
            response = await client.table('friend_follow').insert(request_data).execute()
            return {"success": True, "data": response.data[0] if response.data else None}
        except Exception as e:
            print(f"친구 요청 생성 오류: {e}")
            return {"success": False, "message": "친구 요청 생성 중 오류가 발생했습니다."}
    
    async def get_friend_requests(self, user_id: str) -> List[Dict[str, Any]]:
        """받은 친구 요청 목록 조회"""
        try:
            client = await self._get_client()
            response = await client.table('friend_follow').select('*, request_user:user!friend_follow_request_id_fkey(id, name, email, profile_image)').eq('receiver_id', user_id).eq('follow_status', 'pending').order('requested_at', desc=True).execute()
            return response.data if response.data else []
        except Exception as e:
            print(f"친구 요청 목록 조회 오류: {e}")
            return []
    
    async def accept_friend_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """친구 요청 수락 - 최적화됨"""
        try:
            client = await self._get_client()
            
            # 친구 요청 조회 (필요한 컬럼만 선택)
            request_response = await client.table('friend_follow').select(
                'id, request_id, receiver_id, follow_status'
            ).eq('id', request_id).eq('receiver_id', user_id).limit(1).execute()
            
            if not request_response.data:
                return {"success": False, "message": "친구 요청을 찾을 수 없습니다."}
            
            request = request_response.data[0]
            
            if request['follow_status'] != 'pending':
                return {"success": False, "message": "이미 처리된 요청입니다."}
            
            from_user_id = request['request_id']
            to_user_id = request['receiver_id']
            
            # 이미 친구인지 확인
            existing_friend = await client.table('friend_list').select('id').eq(
                'user_id', from_user_id
            ).eq('friend_id', to_user_id).eq('status', True).limit(1).execute()
            
            if existing_friend.data:
                await client.table('friend_follow').update({"follow_status": "accept"}).eq('id', request_id).execute()
                return {"success": True, "message": "이미 친구입니다.", "from_user_id": from_user_id}
            
            # 요청 상태를 accept로 변경
            await client.table('friend_follow').update({"follow_status": "accept"}).eq('id', request_id).execute()
            
            # 친구 관계 생성 (양방향) - 배치 insert
            now = datetime.now().isoformat()
            await client.table('friend_list').insert([
                {"user_id": from_user_id, "friend_id": to_user_id, "status": True, "created_at": now},
                {"user_id": to_user_id, "friend_id": from_user_id, "status": True, "created_at": now}
            ]).execute()
            
            return {"success": True, "message": "친구 요청을 수락했습니다.", "from_user_id": from_user_id}
        except Exception as e:
            print(f"친구 요청 수락 오류: {e}")
            return {"success": False, "message": "친구 요청 수락 중 오류가 발생했습니다."}
    
    async def accept_friend_request_as_guide(self, request_id: str, guide_user_id: str) -> Dict[str, Any]:
        """튜토리얼 가이드 계정 입장에서 친구 요청 자동 수락"""
        try:
            client = await self._get_client()
            
            request_response = await client.table('friend_follow').select('*').eq('id', request_id).limit(1).execute()
            
            if not request_response.data:
                return {"success": False, "message": "친구 요청을 찾을 수 없습니다."}
            
            request = request_response.data[0]
            
            if request['follow_status'] != 'pending':
                return {"success": False, "message": "이미 처리된 요청입니다."}
            
            existing_friend = await client.table('friend_list').select('id').eq('user_id', request['request_id']).eq('friend_id', request['receiver_id']).eq('status', True).limit(1).execute()
            
            if existing_friend.data:
                await client.table('friend_follow').update({"follow_status": "accept"}).eq('id', request_id).execute()
                return {"success": True, "message": "이미 친구입니다.", "from_user_id": request['request_id']}
            
            await client.table('friend_follow').update({"follow_status": "accept"}).eq('id', request_id).execute()
            
            now = datetime.now().isoformat()
            await client.table('friend_list').insert([
                {"user_id": request['request_id'], "friend_id": request['receiver_id'], "status": True, "created_at": now},
                {"user_id": request['receiver_id'], "friend_id": request['request_id'], "status": True, "created_at": now}
            ]).execute()
            
            return {"success": True, "message": "튜토리얼 친구 요청을 수락했습니다.", "from_user_id": request['request_id']}
        except Exception as e:
            print(f"튜토리얼 친구 요청 수락 오류: {e}")
            return {"success": False, "message": "튜토리얼 친구 요청 수락 중 오류가 발생했습니다."}
    
    async def reject_friend_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """친구 요청 거절"""
        try:
            client = await self._get_client()
            response = await client.table('friend_follow').update({"follow_status": "reject"}).eq('id', request_id).eq('receiver_id', user_id).execute()
            
            if response.data:
                rejected_request = response.data[0]
                return {
                    "success": True, 
                    "message": "친구 요청을 거절했습니다.",
                    "from_user_id": rejected_request['request_id']
                }
            else:
                return {"success": False, "message": "친구 요청을 찾을 수 없습니다."}
        except Exception as e:
            print(f"친구 요청 거절 오류: {e}")
            return {"success": False, "message": "친구 요청 거절 중 오류가 발생했습니다."}
    
    async def get_friends(self, user_id: str) -> List[Dict[str, Any]]:
        """친구 목록 조회"""
        try:
            client = await self._get_client()
            response = await client.table('friend_list').select('*, friend_user:user!friend_list_friend_id_fkey(id, name, email, profile_image)').eq('user_id', user_id).eq('status', True).order('created_at', desc=True).execute()
            return response.data if response.data else []
        except Exception as e:
            print(f"친구 목록 조회 오류: {e}")
            return []
    
    async def delete_friend(self, user_id: str, friend_id: str) -> Dict[str, Any]:
        """친구 삭제"""
        try:
            client = await self._get_client()
            await client.table('friend_list').update({
                "status": False, 
                "updated_at": datetime.now().isoformat()
            }).or_(f"and(user_id.eq.{user_id},friend_id.eq.{friend_id}),and(user_id.eq.{friend_id},friend_id.eq.{user_id})").execute()
            
            return {"success": True, "message": "친구를 삭제했습니다."}
        except Exception as e:
            print(f"친구 삭제 오류: {e}")
            return {"success": False, "message": "친구 삭제 중 오류가 발생했습니다."}
    
    async def search_users(self, query: str, current_user_id: str) -> List[Dict[str, Any]]:
        """사용자 검색 (친구 추가용) - 최적화됨"""
        try:
            client = await self._get_client()
            
            # 1. 먼저 친구 ID 목록 조회
            friends_response = await client.table('friend_list').select('friend_id').eq('user_id', current_user_id).eq('status', True).execute()
            friend_ids = [f['friend_id'] for f in friends_response.data] if friends_response.data else []
            
            # 2. 사용자 검색 시 친구 제외
            user_query = client.table('user').select('id, name, email, profile_image').ilike('name', f'%{query}%').neq('id', current_user_id)
            
            if friend_ids:
                user_query = user_query.not_.in_('id', friend_ids)
            
            response = await user_query.execute()
            return response.data if response.data else []
        except Exception as e:
            print(f"사용자 검색 오류: {e}")
            return []

    async def delete_all_user_data(self, user_id: str) -> None:
        """사용자와 관련된 모든 친구 데이터 삭제 (탈퇴용)"""
        try:
            client = await self._get_client()
            print(f"🗑️ [Friends] 사용자 관련 친구 데이터 삭제 시작: {user_id}")
            
            res_list = await client.table('friend_list').delete().or_(f"user_id.eq.{user_id},friend_id.eq.{user_id}").execute()
            print(f"✅ [Friends] 친구 목록 삭제: {len(res_list.data) if res_list.data else 0}건")
            
            res_follow = await client.table('friend_follow').delete().or_(f"request_id.eq.{user_id},receiver_id.eq.{user_id}").execute()
            print(f"✅ [Friends] 친구 요청 삭제: {len(res_follow.data) if res_follow.data else 0}건")
            
        except Exception as e:
            print(f"❌ [Friends] 데이터 삭제 오류: {str(e)}")
            raise Exception(f"친구 데이터 삭제 실패: {str(e)}")