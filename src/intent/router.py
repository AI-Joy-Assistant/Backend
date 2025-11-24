from fastapi import APIRouter, HTTPException
from src.intent.models import IntentParseRequest, IntentParseResult
from src.intent.service import IntentService

router = APIRouter(prefix="/intent", tags=["Intent"])


@router.post("/parse", response_model=IntentParseResult, summary="메시지 인텐트 파싱")
async def parse_intent(request: IntentParseRequest):
    try:
        result = await IntentService.extract_schedule_info(request.message)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"인텐트 파싱 실패: {str(e)}")

