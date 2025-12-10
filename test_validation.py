
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime

class A2ASessionResponse(BaseModel):
    id: str
    initiator_user_id: str
    target_user_id: str
    status: str
    created_at: datetime
    thread_id: Optional[str] = None
    participant_count: Optional[int] = None
    participant_ids: Optional[List[str]] = None
    participant_names: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None
    title: Optional[str] = None
    summary: Optional[str] = None

# Sample data mimicking what's in a2a_router.py before validation
# This includes extra fields from DB (intent, time_window, place_pref, etc.)
sample_data = {
  "id": "87c8332d-145b-413c-bf2c-b4112e6152b9",
  "initiator_user_id": "f4726814-827b-4c45-bb04-c527faa3ea87",
  "target_user_id": "f2e34339-1a9a-4106-8074-46e59f9facf3",
  "intent": "schedule",
  "status": "in_progress",
  "time_window": {
    "date": "내일",
    "time": None,
    "duration_minutes": 60
  },
  "place_pref": {
    "date": "내일",
    "time": None,
    "summary": "성신조이 내일",
    "activity": "약속",
    "location": "강남에서",
    "thread_id": "25721ea4-0233-415c-a49a-ba831c34962b",
    "participants": [
      "f2e34339-1a9a-4106-8074-46e59f9facf3"
    ]
  },
  "final_event_id": None,
  "created_at": "2025-12-09T17:59:32.7786",
  "updated_at": "2025-12-09T17:59:32.797993",
  
  # Added fields from router logic
  "thread_id": "25721ea4-0233-415c-a49a-ba831c34962b",
  "participant_ids": ["f2e34339-1a9a-4106-8074-46e59f9facf3"],
  "participant_count": 1,
  "participant_names": ["Test User"],
  "title": "Test Title",
  "summary": "Test Summary",
  "details": {
      "proposer": "Test User",
      "proposerAvatar": "",
      "purpose": "Test",
      "proposedDate": "내일",
      "proposedTime": "미정",
      "location": "강남에서",
      "process": []
  }
}

try:
    print("Testing validation...")
    model = A2ASessionResponse(**sample_data)
    print("✅ Validation successful!")
    print(model.model_dump())
except Exception as e:
    print(f"❌ Validation failed: {e}")
