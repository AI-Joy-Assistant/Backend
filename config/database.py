from supabase import create_client, Client
from .settings import settings
import os

# 실제 Supabase 연결 모드인지 확인
is_dev_mode = (
    settings.SUPABASE_URL.startswith("https://your") or 
    settings.SUPABASE_SERVICE_KEY.startswith("your") or
    os.getenv("DEV_MODE", "false").lower() == "true"  # 실제 Supabase 연결 사용
)

if is_dev_mode:
    # 개발용 Mock 클라이언트
    class MockSupabaseClient:
        def table(self, table_name: str):
            return MockTable(table_name)
    
    class MockTable:
        def __init__(self, table_name: str):
            self.table_name = table_name
        
        def select(self, columns: str):
            return MockQuery(self.table_name)
        
        def insert(self, data):
            return MockInsert(data)
        
        def update(self, data):
            return MockUpdate(data)
    
    class MockQuery:
        def __init__(self, table_name: str):
            self.table_name = table_name
            self.conditions = {}
            self.mock_data = None
        
        def eq(self, column: str, value):
            self.conditions[column] = value
            # user 테이블에서 이메일 조회 시 더미 데이터 설정
            if self.table_name == 'user' and column == 'email':
                self.mock_data = [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "email": value,
                        "name": "조수연",
                        "profile_image": "https://example.com/profile.jpg",
                        "created_at": "2024-01-15T10:30:00"
                    }
                ]
            return self
        
        def or_(self, condition: str):
            # a2a 테이블에서 채팅방 조회 시 더미 데이터 설정 (모든 사용자에게 적용)
            if self.table_name == 'a2a':
                self.mock_data = [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440001",
                        "send_id": "550e8400-e29b-41d4-a716-446655440000",
                        "receive_id": "550e8400-e29b-41d4-a716-446655440002",
                        "message": "안녕하세요! 일정 조율을 도와드리겠습니다.",
                        "message_type": "text",
                        "created_at": "2024-01-15T10:30:00"
                    },
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440003", 
                        "send_id": "550e8400-e29b-41d4-a716-446655440002",
                        "receive_id": "550e8400-e29b-41d4-a716-446655440000",
                        "message": "네, 감사합니다. 내일 오후 2시는 어떠세요?",
                        "message_type": "text",
                        "created_at": "2024-01-15T10:35:00"
                    },
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440004",
                        "send_id": "550e8400-e29b-41d4-a716-446655440000", 
                        "receive_id": "550e8400-e29b-41d4-a716-446655440005",
                        "message": "회의실 예약이 완료되었습니다.",
                        "message_type": "text",
                        "created_at": "2024-01-14T09:20:00"
                    }
                ]
            return self
        
        def in_(self, column: str, values):
            # user 테이블에서 이름 조회 시 더미 데이터 설정
            if self.table_name == 'user' and column == 'id':
                self.mock_data = [
                    {"id": "550e8400-e29b-41d4-a716-446655440000", "name": "나"},
                    {"id": "550e8400-e29b-41d4-a716-446655440002", "name": "아구만"},
                    {"id": "550e8400-e29b-41d4-a716-446655440005", "name": "조민지"},
                ]
            return self
        
        def order(self, column: str, desc: bool = False):
            return self
        
        def limit(self, count: int):
            return self
        
        def maybe_single(self):
            return self
        
        def single(self):
            return self
        
        def execute(self):
            return MockResponse(self.mock_data)
    
    class MockInsert:
        def __init__(self, data):
            self.data = data
        
        def execute(self):
            # 전달받은 ID가 있으면 사용, 없으면 기본 UUID
            user_id = self.data.get("id", "550e8400-e29b-41d4-a716-446655440000")
            return MockResponse([{**self.data, "id": user_id}])
    
    class MockUpdate:
        def __init__(self, data):
            self.data = data
        
        def eq(self, column: str, value):
            return self
        
        def execute(self):
            return MockResponse([])
    
    class MockResponse:
        def __init__(self, data):
            self.data = data
    
    supabase = MockSupabaseClient()
    print("🔧 개발 모드: Mock Supabase 클라이언트 사용 중")
else:
    # 실제 Supabase 클라이언트 생성
    supabase: Client = create_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_SERVICE_KEY
    ) 