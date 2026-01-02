from typing import List, Dict, Any, Optional
from supabase import create_client, Client
from config.settings import settings
from datetime import datetime
import uuid

class FriendsRepository:
    def __init__(self):
        self.supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    
    async def get_user_by_email_or_handle(self, identifier: str) -> Optional[Dict[str, Any]]:
        """이메일 또는 handle로 사용자 조회"""
        try:
            print(f"🔍 사용자 검색 시작: identifier='{identifier}'")
            
            # 먼저 이메일로 검색
            print(f"📧 이메일로 검색 중...")
            response = self.supabase.table('user').select('*').eq('email', identifier).execute()
            print(f"📧 이메일 검색 결과: {len(response.data) if response.data else 0}개")
            if response.data:
                print(f"✅ 이메일로 사용자 찾음: {response.data[0].get('name')}")
                return response.data[0]
            
            # 이메일로 찾지 못하면 handle로 검색
            print(f"🏷️ handle로 검색 중...")
            response = self.supabase.table('user').select('*').eq('handle', identifier).execute()
            print(f"🏷️ handle 검색 결과: {len(response.data) if response.data else 0}개")
            if response.data:
                print(f"✅ handle로 사용자 찾음: {response.data[0].get('name')}")
                return response.data[0]
            
            print(f"❌ 사용자를 찾을 수 없음 (이메일/handle 모두 실패)")
            return None
        except Exception as e:
            print(f"사용자 조회 오류: {e}")
            return None
    
    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """사용자 ID로 조회"""
        try:
            response = self.supabase.table('user').select('*').eq('id', user_id).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"사용자 조회 오류: {e}")
            return None
    
    async def create_friend_request(self, from_user_id: str, to_user_id: str) -> Dict[str, Any]:
        """친구 요청 생성"""
        try:
            # 이미 친구 요청이 있는지 확인
            existing_request = self.supabase.table('friend_follow').select('*').eq('request_id', from_user_id).eq('receiver_id', to_user_id).eq('follow_status', 'pending').execute()
            
            if existing_request.data:
                return {"success": False, "message": "이미 친구 요청을 보냈습니다."}
            
            # 이미 친구인지 확인
            existing_friend = self.supabase.table('friend_list').select('*').eq('user_id', from_user_id).eq('friend_id', to_user_id).eq('status', True).execute()
            
            if existing_friend.data:
                return {"success": False, "message": "이미 친구입니다."}
            
            request_data = {
                "request_id": from_user_id,
                "receiver_id": to_user_id,
                "follow_status": "pending",
                "requested_at": datetime.now().isoformat()
            }
            
            response = self.supabase.table('friend_follow').insert(request_data).execute()
            return {"success": True, "data": response.data[0] if response.data else None}
        except Exception as e:
            print(f"친구 요청 생성 오류: {e}")
            return {"success": False, "message": "친구 요청 생성 중 오류가 발생했습니다."}
    
    async def get_friend_requests(self, user_id: str) -> List[Dict[str, Any]]:
        """받은 친구 요청 목록 조회"""
        try:
            response = self.supabase.table('friend_follow').select('*, request_user:user!friend_follow_request_id_fkey(*)').eq('receiver_id', user_id).eq('follow_status', 'pending').order('requested_at', desc=True).execute()
            return response.data
        except Exception as e:
            print(f"친구 요청 목록 조회 오류: {e}")
            return []
    
    async def accept_friend_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """친구 요청 수락"""
        try:
            # 친구 요청 조회
            request_response = self.supabase.table('friend_follow').select('*').eq('id', request_id).eq('receiver_id', user_id).execute()
            
            if not request_response.data:
                return {"success": False, "message": "친구 요청을 찾을 수 없습니다."}
            
            request = request_response.data[0]
            
            # 요청 상태를 accept로 변경
            self.supabase.table('friend_follow').update({"follow_status": "accept"}).eq('id', request_id).execute()
            
            # 친구 관계 생성 (양방향)
            friend_data1 = {
                "user_id": request['request_id'],
                "friend_id": request['receiver_id'],
                "status": True,
                "created_at": datetime.now().isoformat()
            }
            
            friend_data2 = {
                "user_id": request['receiver_id'],
                "friend_id": request['request_id'],
                "status": True,
                "created_at": datetime.now().isoformat()
            }
            
            self.supabase.table('friend_list').insert([friend_data1, friend_data2]).execute()
            
            return {"success": True, "message": "친구 요청을 수락했습니다.", "from_user_id": request['request_id']}
        except Exception as e:
            print(f"친구 요청 수락 오류: {e}")
            return {"success": False, "message": "친구 요청 수락 중 오류가 발생했습니다."}
    
    async def reject_friend_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """친구 요청 거절"""
        try:
            response = self.supabase.table('friend_follow').update({"follow_status": "reject"}).eq('id', request_id).eq('receiver_id', user_id).execute()
            
            if response.data:
                return {"success": True, "message": "친구 요청을 거절했습니다."}
            else:
                return {"success": False, "message": "친구 요청을 찾을 수 없습니다."}
        except Exception as e:
            print(f"친구 요청 거절 오류: {e}")
            return {"success": False, "message": "친구 요청 거절 중 오류가 발생했습니다."}
    
    async def get_friends(self, user_id: str) -> List[Dict[str, Any]]:
        """친구 목록 조회"""
        try:
            response = self.supabase.table('friend_list').select('*, friend_user:user!friend_list_friend_id_fkey(*)').eq('user_id', user_id).eq('status', True).order('created_at', desc=True).execute()
            return response.data
        except Exception as e:
            print(f"친구 목록 조회 오류: {e}")
            return []
    
    async def delete_friend(self, user_id: str, friend_id: str) -> Dict[str, Any]:
        """친구 삭제"""
        try:
            # 양방향 친구 관계를 비활성화 (status = False)
            # PostgREST syntax for OR with AND groups: or=(and(user_id.eq.A,friend_id.eq.B),and(user_id.eq.B,friend_id.eq.A))
            self.supabase.table('friend_list').update({
                "status": False, 
                "updated_at": datetime.now().isoformat()
            }).or_(f"and(user_id.eq.{user_id},friend_id.eq.{friend_id}),and(user_id.eq.{friend_id},friend_id.eq.{user_id})").execute()
            
            return {"success": True, "message": "친구를 삭제했습니다."}
        except Exception as e:
            print(f"친구 삭제 오류: {e}")
            return {"success": False, "message": "친구 삭제 중 오류가 발생했습니다."}
    
    async def search_users(self, query: str, current_user_id: str) -> List[Dict[str, Any]]:
        """사용자 검색 (친구 추가용)"""
        try:
            # 현재 사용자와 친구가 아닌 사용자들 검색
            response = self.supabase.table('user').select('*').ilike('name', f'%{query}%').neq('id', current_user_id).execute()
            
            # 친구 관계 확인하여 이미 친구인 사용자 제외
            friends_response = self.supabase.table('friend_list').select('friend_id').eq('user_id', current_user_id).eq('status', True).execute()
            friend_ids = [f['friend_id'] for f in friends_response.data]
            
            # 친구가 아닌 사용자만 필터링
            filtered_users = [user for user in response.data if user['id'] not in friend_ids]
            
            return filtered_users
        except Exception as e:
            print(f"사용자 검색 오류: {e}")
            return []

    async def delete_all_user_data(self, user_id: str) -> None:
        """사용자와 관련된 모든 친구 데이터 삭제 (탈퇴용)"""
        try:
            print(f"🗑️ [Friends] 사용자 관련 친구 데이터 삭제 시작: {user_id}")
            
            # 1. friend_list 삭제 (user_id 또는 friend_id가 해당 사용자인 경우)
            # 관계 끊기(status=False)가 아니라 실제 데이터 삭제
            res_list = (
                self.supabase
                .table('friend_list')
                .delete()
                .or_(f"user_id.eq.{user_id},friend_id.eq.{user_id}")
                .execute()
            )
            print(f"✅ [Friends] 친구 목록 삭제: {len(res_list.data) if res_list.data else 0}건")
            
            # 2. friend_follow 삭제 (request_id 또는 receiver_id가 해당 사용자인 경우)
            res_follow = (
                self.supabase
                .table('friend_follow')
                .delete()
                .or_(f"request_id.eq.{user_id},receiver_id.eq.{user_id}")
                .execute()
            )
            print(f"✅ [Friends] 친구 요청 삭제: {len(res_follow.data) if res_follow.data else 0}건")
            
        except Exception as e:
            print(f"❌ [Friends] 데이터 삭제 오류: {str(e)}")
            raise Exception(f"친구 데이터 삭제 실패: {str(e)}") 