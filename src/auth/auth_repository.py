from typing import Optional, Dict, Any
from config.database import get_async_supabase
from .auth_models import User, UserCreate

class AuthRepository:
    """인증 관련 데이터베이스 작업 - Async 버전"""
    
    @staticmethod
    async def _get_client():
        """비동기 Supabase 클라이언트 반환"""
        return await get_async_supabase()
    
    @staticmethod
    async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
        """이메일로 사용자 찾기"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').select('*').eq('email', email).limit(1).execute()
            if not response.data:
                return None
            return response.data[0]
        except Exception as e:
            print(f"❌ 이메일로 사용자 조회 오류: {str(e)}")
            raise Exception(f"사용자 조회 오류: {str(e)}")

    @staticmethod
    async def find_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """ID로 사용자 찾기 - 최적화됨"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').select('id, email, name, profile_image, handle, created_at').eq('id', user_id).limit(1).execute()
            if not response.data:
                return None
            return response.data[0]
        except Exception as e:
            print(f"❌ ID로 사용자 조회 오류: {str(e)}")
            return None

    @staticmethod
    async def find_user_by_apple_id(apple_id: str) -> Optional[Dict[str, Any]]:
        """Apple ID로 사용자 찾기"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').select('*').eq('apple_id', apple_id).limit(1).execute()
            if not response.data:
                return None
            return response.data[0]
        except Exception as e:
            print(f"❌ Apple ID로 사용자 조회 오류: {str(e)}")
            return None

    @staticmethod
    async def create_user(user_data: Dict[str, str]) -> Dict[str, Any]:
        """새 사용자 생성"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').insert(user_data).execute()
            if not response.data:
                raise Exception("사용자 생성 실패: response.data is empty")
            return response.data[0]
        except Exception as e:
            print(f"❌ 사용자 생성 오류: {str(e)}")
            raise Exception(f"사용자 생성 오류: {str(e)}")

    @staticmethod
    async def update_user_status(email: str, status: bool) -> None:
        """사용자 상태 업데이트"""
        try:
            client = await AuthRepository._get_client()
            await client.table('user').update({'status': status, 'updated_at': 'NOW()'}).eq('email', email).execute()
        except Exception as e:
            print(f"⚠️ 사용자 상태 업데이트 오류: {str(e)}")

    @staticmethod
    async def find_by_refresh_token(refresh_token: str) -> Optional[Dict[str, Any]]:
        """리프레시 토큰으로 사용자 찾기"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').select('*').eq('refresh_token', refresh_token).limit(1).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            raise Exception(f"리프레시 토큰으로 사용자 조회 오류: {str(e)}")

    @staticmethod
    async def update_tokens(user_id: str, access_token: Optional[str] = None, refresh_token: Optional[str] = None) -> None:
        """액세스 토큰과 리프레시 토큰 업데이트"""
        try:
            client = await AuthRepository._get_client()
            update_data = {'updated_at': 'NOW()'}
            if access_token is not None:
                update_data['access_token'] = access_token
            if refresh_token is not None:
                update_data['refresh_token'] = refresh_token
            await client.table('user').update(update_data).eq('id', user_id).execute()
        except Exception as e:
            raise Exception(f"토큰 업데이트 오류: {str(e)}")

    @staticmethod
    async def update_refresh_token(user_id: str, refresh_token: Optional[str]) -> None:
        """리프레시 토큰 업데이트"""
        try:
            client = await AuthRepository._get_client()
            await client.table('user').update({'refresh_token': refresh_token, 'updated_at': 'NOW()'}).eq('id', user_id).execute()
        except Exception as e:
            raise Exception(f"리프레시 토큰 업데이트 오류: {str(e)}")

    @staticmethod
    async def clear_refresh_token(user_id: str) -> None:
        """리프레시 토큰 삭제"""
        try:
            client = await AuthRepository._get_client()
            await client.table('user').update({'refresh_token': None, 'updated_at': 'NOW()'}).eq('id', user_id).execute()
        except Exception as e:
            raise Exception(f"리프레시 토큰 삭제 오류: {str(e)}")

    @staticmethod
    async def create_google_user(user_data: Dict[str, str]) -> Dict[str, Any]:
        """Google OAuth 사용자 생성"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').insert(user_data).execute()
            if not response.data:
                raise Exception("Google 사용자 생성 실패: response.data is empty")
            return response.data[0]
        except Exception as e:
            print(f"❌ Google 사용자 생성 오류: {str(e)}")
            raise Exception(f"Google 사용자 생성 오류: {str(e)}")

    @staticmethod
    async def update_google_user_info(
        email: str, 
        access_token: Optional[str] = None, 
        refresh_token: Optional[str] = None,
        profile_image: Optional[str] = None,
        name: Optional[str] = None,
        handle: Optional[str] = None,
        token_expiry: Optional[str] = None
    ) -> None:
        """Google 사용자 정보 업데이트"""
        try:
            client = await AuthRepository._get_client()
            update_data = {'updated_at': 'NOW()'}
            if access_token is not None:
                update_data['access_token'] = access_token
            if refresh_token is not None:
                update_data['refresh_token'] = refresh_token
            if profile_image is not None:
                update_data['profile_image'] = profile_image
            if name is not None:
                update_data['name'] = name
            if handle is not None:
                update_data['handle'] = handle
            if token_expiry is not None:
                update_data['token_expiry'] = token_expiry
            
            await client.table('user').update(update_data).eq('email', email).execute()
        except Exception as e:
            print(f"❌ Google 사용자 정보 업데이트 오류: {str(e)}")
            raise Exception(f"Google 사용자 정보 업데이트 오류: {str(e)}")

    @staticmethod
    async def update_user(user_id: str, user_data: dict) -> Dict[str, Any]:
        """사용자 정보 수정"""
        try:
            client = await AuthRepository._get_client()
            response = await client.table('user').update(user_data).eq('id', user_id).execute()
            if not response.data:
                raise Exception("사용자 정보 수정 실패: response is None or empty")
            return response.data[0]
        except Exception as e:
            print(f"❌ 사용자 정보 수정 오류: {str(e)}")
            raise Exception(f"사용자 정보 수정 오류: {str(e)}")

    @staticmethod
    async def delete_user(user_id: str) -> None:
        """사용자 계정 삭제"""
        try:
            client = await AuthRepository._get_client()
            await client.table('user').delete().eq('id', user_id).execute()
            print(f"✅ 사용자 계정 삭제 성공: {user_id}")
        except Exception as e:
            print(f"❌ 사용자 계정 삭제 오류: {str(e)}")
            raise Exception(f"사용자 계정 삭제 오류: {str(e)}")