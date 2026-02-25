import asyncio
from src.database.supabase_client import get_supabase_client

async def check():
    client = get_supabase_client()
    resp = client.table("calendar_event").select("*").order("created_at", desc=True).limit(5).execute()
    for row in resp.data:
        print(f"ID: {row['id']}, GoogleID: {row.get('google_event_id')}, Summary: {row['summary']}, IsAllDay: {row.get('is_all_day_event')}")

asyncio.run(check())
