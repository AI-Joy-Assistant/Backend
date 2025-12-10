
import asyncio
import os
import sys
import json
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.database import get_supabase_client

class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for handling datetime objects"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

async def debug_a2a():
    try:
        supabase = get_supabase_client()
        print("Fetching a2a_session records...")
        
        # Fetch up to 5 records
        response = supabase.table('a2a_session').select('*').limit(5).order('created_at', desc=True).execute()
        
        if not response.data:
            print("No records found in a2a_session")
            return

        print(f"Found {len(response.data)} records.")
        for i, record in enumerate(response.data):
            print(f"\n--- Record {i+1} ---")
            print(json.dumps(record, indent=2, ensure_ascii=False, cls=DateTimeEncoder))
            
            # Check for required fields in A2ASessionResponse
            required_fields = ['id', 'initiator_user_id', 'target_user_id', 'status', 'created_at']
            missing = [f for f in required_fields if f not in record]
            if missing:
                print(f"⚠️ MISSING REQUIRED FIELDS: {missing}")
            else:
                print("✅ All required fields present")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Redirect stdout to file
    with open("debug_log_json.txt", "w", encoding="utf-8") as f:
        sys.stdout = f
        asyncio.run(debug_a2a())
