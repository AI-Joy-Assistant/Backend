from typing import Optional, Dict, Any
from config.database import supabase
from .models import User, UserCreate

class AuthRepository:
    @staticmethod
    async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
        """이메일로 사용자 찾기"""
        try:
            response = supabase.table('user').select('*').eq('email', email).maybe_single().execute()
            if response is None:
                return None
            return response.data
        except Exception as e:
            raise Exception(f"사용자 조회 오류: {str(e)}")

    @staticmethod
    async def create_user(user_data: Dict[str, str]) -> Dict[str, Any]:
        """새 사용자 생성"""
        try:
            response = supabase.table('user').insert(user_data).execute()
            if response is None:
                raise Exception("사용자 생성 실패: response is None")
            if response.data:
                return response.data[0]
            raise Exception("사용자 생성 실패: response.data is empty")
        except Exception as e:
            raise Exception(f"사용자 생성 오류: {str(e)}")

    @staticmethod
    async def update_user_status(email: str, status: bool) -> None:
        """사용자 상태 업데이트"""
        try:
            response = supabase.table('user').update({'status': status, 'updated_at': 'NOW()'}).eq('email', email).execute()
            if response is None:
                print(f"⚠️ 사용자 상태 업데이트 실패: response is None for email {email}")
        except Exception as e:
            print(f"⚠️ 사용자 상태 업데이트 오류: {str(e)}")
            # 상태 업데이트 실패는 치명적이지 않으므로 예외를 발생시키지 않음

    @staticmethod
    async def find_by_refresh_token(refresh_token: str) -> Optional[Dict[str, Any]]:
        """리프레시 토큰으로 사용자 찾기"""
        try:
            response = supabase.table('user').select('*').eq('refresh_token', refresh_token).single().execute()
            return response.data
        except Exception as e:
            raise Exception(f"리프레시 토큰으로 사용자 조회 오류: {str(e)}")

    @staticmethod
    async def update_tokens(user_id: str, access_token: Optional[str] = None, refresh_token: Optional[str] = None) -> None:
        """액세스 토큰과 리프레시 토큰 업데이트"""
        try:
            update_data = {'updated_at': 'NOW()'}
            if access_token is not None:
                update_data['access_token'] = access_token
            if refresh_token is not None:
                update_data['refresh_token'] = refresh_token
            
            supabase.table('user').update(update_data).eq('id', user_id).execute()
        except Exception as e:
            raise Exception(f"토큰 업데이트 오류: {str(e)}")

    @staticmethod
    async def update_refresh_token(user_id: str, refresh_token: Optional[str]) -> None:
        """리프레시 토큰 업데이트"""
        try:
            supabase.table('user').update({'refresh_token': refresh_token, 'updated_at': 'NOW()'}).eq('id', user_id).execute()
        except Exception as e:
            raise Exception(f"리프레시 토큰 업데이트 오류: {str(e)}")

    @staticmethod
    async def clear_refresh_token(user_id: str) -> None:
        """리프레시 토큰 삭제"""
        try:
            supabase.table('user').update({'refresh_token': None, 'updated_at': 'NOW()'}).eq('id', user_id).execute()
        except Exception as e:
            raise Exception(f"리프레시 토큰 삭제 오류: {str(e)}") 