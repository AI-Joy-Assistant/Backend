from supabase import create_client, Client
from .settings import settings

# 실제 Supabase 클라이언트 생성
supabase: Client = create_client(
    supabase_url=settings.SUPABASE_URL,
    supabase_key=settings.SUPABASE_SERVICE_KEY
)

def get_supabase_client():
    """Supabase 클라이언트를 반환합니다."""
    return supabase 