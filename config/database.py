# Async Supabase 클라이언트 설정
# supabase-py 2.x 버전에서는 acreate_client 사용

from supabase import create_client, Client
from supabase._async.client import AsyncClient, create_client as acreate_client
from .settings import settings

# 동기 클라이언트 (레거시 호환용 - 점진적 마이그레이션)
supabase: Client = create_client(
    supabase_url=settings.SUPABASE_URL,
    supabase_key=settings.SUPABASE_SERVICE_KEY
)

# 비동기 클라이언트 (싱글톤 패턴)
_async_client: AsyncClient = None

async def get_async_supabase() -> AsyncClient:
    """비동기 Supabase 클라이언트 반환 (싱글톤)"""
    global _async_client
    if _async_client is None:
        _async_client = await acreate_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_SERVICE_KEY
        )
    return _async_client

def get_supabase_client():
    """동기 Supabase 클라이언트를 반환합니다. (레거시)"""
    return supabase