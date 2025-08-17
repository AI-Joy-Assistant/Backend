import openai
from typing import Dict, Any, List
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
    
    async def generate_response(self, user_message: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """ChatGPT API를 사용하여 응답 생성"""
        try:
            # 시스템 프롬프트 설정
            system_prompt = """당신은 AI Joy Assistant의 일정 조율 도우미입니다. 
사용자와 친구들의 일정을 조율하고 약속을 잡는 것을 도와주세요.

주요 기능:
1. 친구와의 일정 조율
2. 약속 시간 및 장소 제안
3. 일정 충돌 확인
4. 친근하고 도움이 되는 대화

항상 친근하고 도움이 되는 톤으로 응답하세요."""

            # 대화 히스토리 구성
            messages = [{"role": "system", "content": system_prompt}]
            
            if conversation_history:
                for msg in conversation_history[-10:]:  # 최근 10개 메시지만 포함
                    if msg.get("type") == "user":
                        messages.append({"role": "user", "content": msg["message"]})
                    elif msg.get("type") == "assistant":
                        messages.append({"role": "assistant", "content": msg["message"]})
            
            # 현재 사용자 메시지 추가
            messages.append({"role": "user", "content": user_message})
            
            # ChatGPT API 호출
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=500,
                temperature=0.7
            )
            
            ai_response = response.choices[0].message.content
            
            logger.info(f"OpenAI API 응답 생성 완료: {len(ai_response)}자")
            
            return {
                "status": "success",
                "message": ai_response,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }
            }
            
        except Exception as e:
            logger.error(f"OpenAI API 호출 실패: {str(e)}")
            return {
                "status": "error",
                "message": "죄송합니다. 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                "error": str(e)
            }
    
    async def extract_schedule_info(self, message: str) -> Dict[str, Any]:
        """메시지에서 일정 관련 정보 추출"""
        try:
            system_prompt = """다음 메시지에서 일정 관련 정보를 추출해주세요.
JSON 형태로 다음 정보를 반환하세요:
{
    "friend_name": "친구 이름",
    "date": "날짜 (오늘, 내일, 모레, 특정 날짜)",
    "time": "시간 (점심, 저녁, 특정 시간)",
    "activity": "활동 내용",
    "location": "장소 (있다면)",
    "has_schedule_request": true/false
}

예시:
- "아구만이랑 내일 점심 약속 잡아줘" → {"friend_name": "아구만", "date": "내일", "time": "점심", "activity": "약속", "has_schedule_request": true}
- "안녕하세요" → {"has_schedule_request": false}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                max_tokens=200,
                temperature=0.1
            )
            
            # JSON 응답 파싱 시도
            try:
                import json
                result = json.loads(response.choices[0].message.content)
                return result
            except json.JSONDecodeError:
                # JSON 파싱 실패 시 기본 정보만 반환
                return {
                    "has_schedule_request": False,
                    "message": response.choices[0].message.content
                }
                
        except Exception as e:
            logger.error(f"일정 정보 추출 실패: {str(e)}")
            return {
                "has_schedule_request": False,
                "error": str(e)
            }
