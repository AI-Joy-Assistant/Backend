from typing import Optional, Dict, Any
from config.database import supabase
from .models import User, UserCreate

class AuthRepository:
    @staticmethod
    async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
        """이메일로 사용자 찾기"""
        try:
            print(f"🔍 이메일로 사용자 조회: {email}")
            response = supabase.table('user').select('*').eq('email', email).maybe_single().execute()
            if response is None:
                print(f"❌ 이메일로 사용자를 찾을 수 없음: {email}")
                return None
            print(f"✅ 이메일로 사용자 조회 성공: {response.data.get('email')}")
            print(f"📸 프로필 이미지: {response.data.get('profile_image')}")
            return response.data
        except Exception as e:
            print(f"❌ 이메일로 사용자 조회 오류: {str(e)}")
            raise Exception(f"사용자 조회 오류: {str(e)}")

    @staticmethod
    async def find_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """ID로 사용자 찾기"""
        try:
            print(f"🔍 ID로 사용자 조회: {user_id}")
            response = supabase.table('user').select('*').eq('id', user_id).maybe_single().execute()
            if response is None:
                print(f"❌ ID로 사용자를 찾을 수 없음: {user_id}")
                return None
            print(f"✅ ID로 사용자 조회 성공: {response.data.get('email')}")
            return response.data
        except Exception as e:
            print(f"❌ ID로 사용자 조회 오류: {str(e)}")
            raise Exception(f"사용자 조회 오류: {str(e)}")

    @staticmethod
    async def create_user(user_data: Dict[str, str]) -> Dict[str, Any]:
        """새 사용자 생성"""
        try:
            print(f"🆕 사용자 생성 시작: {user_data.get('email')}")
            print(f"📝 저장할 데이터: {user_data}")
            
            response = supabase.table('user').insert(user_data).execute()
            print(f"📊 Supabase 응답: {response}")
            
            if response is None:
                print("❌ Supabase 응답이 None")
                raise Exception("사용자 생성 실패: response is None")
            if response.data:
                print(f"✅ 사용자 생성 성공: {response.data[0].get('id')}")
                return response.data[0]
            print("❌ Supabase 응답 데이터가 비어있음")
            raise Exception("사용자 생성 실패: response.data is empty")
        except Exception as e:
            print(f"❌ 사용자 생성 오류: {str(e)}")
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

    @staticmethod
    async def create_google_user(user_data: Dict[str, str]) -> Dict[str, Any]:
        """Google OAuth 사용자 생성 (email 기반)"""
        try:
            print(f"🆕 Google 사용자 생성 시작: {user_data.get('email')}")
            print(f"📝 저장할 데이터: {user_data}")
            
            response = supabase.table('user').insert(user_data).execute()
            print(f"📊 Supabase 응답: {response}")
            
            if response is None:
                print("❌ Supabase 응답이 None")
                raise Exception("Google 사용자 생성 실패: response is None")
            if response.data:
                print(f"✅ Google 사용자 생성 성공: {response.data[0].get('id')}")
                return response.data[0]
            print("❌ Supabase 응답 데이터가 비어있음")
            raise Exception("Google 사용자 생성 실패: response.data is empty")
        except Exception as e:
            print(f"❌ Google 사용자 생성 오류: {str(e)}")
            raise Exception(f"Google 사용자 생성 오류: {str(e)}")

    @staticmethod
    async def update_google_user_info(
        email: str, 
        access_token: Optional[str] = None, 
        refresh_token: Optional[str] = None,
        profile_image: Optional[str] = None,
        name: Optional[str] = None
    ) -> None:
        """Google 사용자 정보 업데이트"""
        try:
            print(f"🔄 Google 사용자 정보 업데이트: {email}")
            
            update_data = {'updated_at': 'NOW()'}
            if access_token is not None:
                update_data['access_token'] = access_token
                print(f"✅ access_token 추가됨: {len(access_token)}자")
            if refresh_token is not None:
                update_data['refresh_token'] = refresh_token
                print(f"✅ refresh_token 추가됨: {len(refresh_token)}자")
            if profile_image is not None:
                update_data['profile_image'] = profile_image
                print(f"✅ profile_image 추가됨: {profile_image[:50]}...")
            if name is not None:
                update_data['name'] = name
                print(f"✅ name 추가됨: {name}")
            else:
                print(f"ℹ️ name은 업데이트하지 않음 (기존 닉네임 유지)")
            
            print(f"📝 업데이트할 데이터: {update_data}")
            
            response = supabase.table('user').update(update_data).eq('email', email).execute()
            print(f"✅ Google 사용자 정보 업데이트 성공")
            
        except Exception as e:
            print(f"❌ Google 사용자 정보 업데이트 오류: {str(e)}")
            raise Exception(f"Google 사용자 정보 업데이트 오류: {str(e)}")

    @staticmethod
    async def update_user(user_id: str, user_data: dict) -> Dict[str, Any]:
        """사용자 정보 수정"""
        try:
            print(f"🔄 사용자 정보 수정 시작: {user_id}")
            print(f"📝 수정할 데이터: {user_data}")
            
            response = supabase.table('user').update(user_data).eq('id', user_id).execute()
            
            if response is None or not response.data:
                raise Exception("사용자 정보 수정 실패: response is None or empty")
            
            print(f"✅ 사용자 정보 수정 성공: {user_id}")
            return response.data[0]
        except Exception as e:
            print(f"❌ 사용자 정보 수정 오류: {str(e)}")
            raise Exception(f"사용자 정보 수정 오류: {str(e)}")

    @staticmethod
    async def delete_user(user_id: str) -> None:
        """사용자 계정 삭제"""
        try:
            print(f"🗑️ 사용자 계정 삭제 시작: {user_id}")
            
            response = supabase.table('user').delete().eq('id', user_id).execute()
            
            if response is None:
                raise Exception("사용자 계정 삭제 실패: response is None")
            
            print(f"✅ 사용자 계정 삭제 성공: {user_id}")
        except Exception as e:
            print(f"❌ 사용자 계정 삭제 오류: {str(e)}")
            raise Exception(f"사용자 계정 삭제 오류: {str(e)}") 