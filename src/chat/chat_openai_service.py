import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
import os
import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
    
    async def request_chat_completion(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 200) -> str:
        """Llama 또는 OpenAI 모델을 사용하여 채팅 응답 생성 (통합 메서드)"""
        # Llama API 우선 사용
        if settings.LLM_API_URL or os.getenv("LLM_API_URL"):
            return await self._call_custom_model(messages, temperature, max_tokens)
        
        # OpenAI 폴백
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
        )
        return response.choices[0].message.content.strip()

    async def _call_custom_model(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 500) -> str:
        """커스텀 LLM (Llama 등) 호출 - 새 API 스펙"""
        url = settings.LLM_API_URL or os.getenv("LLM_API_URL")
        if not url:
            raise ValueError("LLM_API_URL not set")

        # 새 API 스펙: messages 배열 그대로 전송
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        logger.info(f"[Llama API] 요청 전송: {url}")
        logger.debug(f"[Llama API] Payload: {len(messages)}개 메시지")
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "")
            logger.info(f"[Llama API] 응답 수신: {len(response_text)}자")
            return response_text

    def _get_current_time_info(self) -> str:
        """현재 시간 정보를 문자열로 반환"""
        KST = ZoneInfo("Asia/Seoul")
        now = datetime.now(KST)
        
        # 요일을 한글로 변환
        weekday_map = {
            0: "월요일",
            1: "화요일", 
            2: "수요일",
            3: "목요일",
            4: "금요일",
            5: "토요일",
            6: "일요일"
        }
        
        weekday_kr = weekday_map[now.weekday()]
        return now.strftime(f"%Y년 %m월 %d일 {weekday_kr} %H시 %M분 (한국 시간)")
    
    async def generate_response(self, user_message: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """ChatGPT API를 사용하여 응답 생성"""
        try:
            current_time = self._get_current_time_info()
            
            system_prompt = f"""당신은 일정 도우미 JOYNER입니다.
현재 시간: {current_time}

## 절대 규칙 (반드시 지켜야 함!)
1. 사용자가 말하지 않은 정보를 만들어내지 마세요!
2. 친구 이름이 없으면 개인 일정입니다
3. 장소, 시간은 사용자가 말한 것만 사용하세요
4. 기존 일정을 취소하거나 변경하지 마세요 (사용자가 요청하지 않았다면)

## 응답 방식
- 짧고 친근하게 답하세요 (1-2문장)
- "아닝", "아니" = 부정/모름 (인사 아님!)
- 필요한 정보가 없으면 간단히 물어보세요

## 일정 등록 흐름
1. 날짜 확인 (없으면 물어보기)
2. 시간 확인 (없으면 물어보기)  
3. 확인 후 등록

예시:
사용자: "내일 3시에 치과 예약 일정 추가해줘"
AI: "네, 내일 오후 3시에 '치과 예약' 일정을 등록할게요. 끝나는 시간도 알려주실래요?"
사용자: "몰라"
AI: "알겠습니다! 내일 오후 3시 '치과 예약' 일정으로 등록했어요 ✅" """

            messages = [{"role": "system", "content": system_prompt}]
            
            if conversation_history:
                # 최근 10개 대화를 컨텍스트로 사용 (TPM 제한 고려하여 축소)
                recent_history = conversation_history[-10:]
                logger.info(f"[OpenAI] 대화 히스토리 {len(recent_history)}개 사용")
                for msg in recent_history:
                    if msg.get("type") == "user":
                        messages.append({"role": "user", "content": msg["message"]})
                        logger.debug(f"[OpenAI] 히스토리 - User: {msg['message'][:50]}...")
                    elif msg.get("type") == "assistant":
                        messages.append({"role": "assistant", "content": msg["message"]})
                        logger.debug(f"[OpenAI] 히스토리 - AI: {msg['message'][:50]}...")
            
            messages.append({"role": "user", "content": user_message})
            logger.info(f"[OpenAI] 현재 메시지: {user_message}")
            
            # Llama API 우선 사용
            if settings.LLM_API_URL or os.getenv("LLM_API_URL"):
                ai_response = await self._call_custom_model(messages, temperature=0.5, max_tokens=300)
                logger.info(f"[Llama API] 원본 응답: {ai_response[:100]}...")
                
                # JSON 응답인 경우 message 필드 추출
                if ai_response.strip().startswith("{"):
                    try:
                        parsed = json.loads(ai_response)
                        if isinstance(parsed, dict) and "message" in parsed:
                            ai_response = parsed["message"]
                            logger.info(f"[Llama API] JSON에서 message 추출: {ai_response[:50]}...")
                    except json.JSONDecodeError:
                        pass  # JSON 아니면 그대로 사용
                
                return {
                    "status": "success",
                    "message": ai_response,
                    "usage": {}
                }

            response = await self.client.chat.completions.create(
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
            error_msg = str(e)
            logger.error(f"OpenAI API 호출 실패: {error_msg}")
            
            user_msg = "죄송해요, 지금 잠시 생각이 안 나네요. 잠시 후 다시 말해주시겠어요?"
            if "insufficient_quota" in error_msg:
                user_msg = "API 사용량 한도가 초과되었어요. 관리자에게 문의해주세요."
            elif "rate limit" in error_msg.lower():
                user_msg = "지금 너무 많은 대화가 오고 가고 있어요. 잠시만 기다려주세요!"
                
            return {
                "status": "error",
                "message": user_msg,
                "error": error_msg
            }
    
    async def extract_schedule_info(self, message: str) -> Dict[str, Any]:
        """메시지에서 일정 관련 정보 추출"""
        try:
            current_time = self._get_current_time_info()
            
            # 현재 시간 상세 정보 (YYYY-MM-DD 형식 포함)
            now_dt = datetime.now(ZoneInfo("Asia/Seoul"))
            today_str = now_dt.strftime("%Y-%m-%d")
            
            system_prompt = f"""다음 메시지에서 일정 관련 정보를 추출해주세요.
현재 시각: {current_time}
오늘 날짜(기준): {today_str}

**중요: 반드시 유효한 JSON만 반환하세요.**

JSON 반환 형식:
{{
    "friend_name": "친구 이름 (⚠️ 언급 없으면 반드시 null!)",
    "friend_names": ["친구1", "친구2"],
    "date": "텍스트 날짜 (예: 이번주 금요일)",
    "start_date": "YYYY-MM-DD (범위 시작)",
    "end_date": "YYYY-MM-DD (범위 종료)",
    "time": "시간 텍스트 (예: 저녁)",
    "start_time": "HH:MM (24시간제)",
    "end_time": "HH:MM (24시간제)",
    "activity": "활동 내용",
    "title": "일정 제목",
    "location": "장소",
    "has_schedule_request": true/false,
    "missing_fields": ["date", "time", "location"] (누락된 필수 정보 리스트)
}}

## ⚠️ 0. 가장 중요한 규칙: 정보를 만들어내지 마세요!
- **사용자가 친구 이름을 명시적으로 말하지 않았다면 friend_name은 반드시 null이어야 합니다!**
- "민서", "규민", "지혜" 등 직접 언급되지 않은 이름을 지어내면 안 됩니다.
- 예시: "내일 한시에 티켓팅 예약하기 일정 등록해줘" → friend_name: null (친구 언급 없음!)
- 예시: "민서랑 내일 밥 먹자" → friend_name: "민서" (친구 언급됨)

## 1. 날짜 범위 변환 규칙 (오늘: {today_str} 기준)
- "이번 달": 오늘부터 이번 달 말일까지 (start_date ~ end_date)
- "다음 주": 다음 주 월요일 ~ 일요일
- "주말": 이번 주 토요일 ~ 일요일 (이미 지났으면 다음 주 주말)
- "평일": 월~금
- "오늘": 오늘 날짜
- "내일": 오늘 + 1일

## 2. 시간 변환 규칙 (매우 중요!)
- **분 단위도 정확히 추출하세요!**
  - "5시반" = "17:30" (반=30분)
  - "5시 30분" = "17:30"
  - "5:30" = "17:30"
  - "5시 15분" = "17:15"
  - "3시 45분" = "15:45"
- "아침": start_time="09:00", end_time="11:00"
- "점심": start_time="12:00", end_time="14:00"
- "저녁": start_time="18:00", end_time="22:00"
- **"오후" + 숫자**: 반드시 12를 더하세요!
  - "오후 1시" = "13:00"
  - "오후 2시" = "14:00"
  - "오후 3시" = "15:00"
  - "오후 3시 30분" = "15:30"
  - "오후 6시" = "18:00"
  - "오후 9시" = "21:00" (절대 18:00이 아님!)
  - "오후 12시" = "12:00" (예외: 12는 그대로)
- "오전 10시" = "10:00"
- "오전 9시" = "09:00"
- "오전 10시반" = "10:30"
- "오후"만 있으면 (숫자 없이): start_time="14:00", end_time="18:00"

## 3. 필수 정보 확인 (Slot Filling)
- 약속을 잡으려는 의도가 명확한데 정보가 빠진 경우 `missing_fields`에 추가하세요.
- 단순히 "언제 볼까?" 같이 탐색하는 단계면 `time`, `location`은 missing이 아님.
- "내일 보자" -> date는 있지만 time, location이 없으므로 missing_fields=["time", "location"] 가능.

## 예시
- "이번 달 안에 민서랑 밥 먹자" -> 
  {{
    "friend_name": "민서", 
    "date": "이번 달", 
    "start_date": "{today_str}", 
    "end_date": "{(now_dt.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1):%Y-%m-%d}", 
    "missing_fields": ["time", "location"],
    "title": "민서와 식사", 
    "has_schedule_request": true
  }}
- "내일 오후 5시 강남역" -> 
  {{ 
    "friend_name": null,
    "date": "내일", "start_date": "{(now_dt + timedelta(days=1)):%Y-%m-%d}", 
    "time": "오후 5시", "start_time": "17:00", 
    "location": "강남역", 
    "missing_fields": [], 
    "has_schedule_request": true
  }}
- "내일 한시에 티켓팅 예약하기 일정 등록해줘" ->
  {{
    "friend_name": null,
    "date": "내일", "start_date": "{(now_dt + timedelta(days=1)):%Y-%m-%d}",
    "time": "한시", "start_time": "13:00",
    "title": "티켓팅 예약하기",
    "has_schedule_request": true
  }}

**반드시 JSON 형식만 반환하세요.**"""

            # Llama API 우선 사용
            if settings.LLM_API_URL or os.getenv("LLM_API_URL"):
                content = await self._call_custom_model(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    temperature=0.1,
                    max_tokens=200
                )
                logger.info(f"[Llama API] 일정 정보 추출 완료")
            else:
                # OpenAI 폴백
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    max_tokens=200,
                    temperature=0.1
                )
                content = response.choices[0].message.content
            
            try:
                content = content.strip()
                # JSON 코드 블록 제거 (```json ... ``` 형태)
                if content.startswith("```"):
                    # 첫 번째 ``` 이후부터 마지막 ``` 이전까지 추출
                    lines = content.split("\n")
                    json_lines = []
                    in_json = False
                    for line in lines:
                        if line.strip().startswith("```"):
                            in_json = not in_json
                            continue
                        if in_json:
                            json_lines.append(line)
                    content = "\n".join(json_lines)
                
                result = json.loads(content)
                # 필수 필드 확인
                if "has_schedule_request" not in result:
                    result["has_schedule_request"] = bool(result.get("friend_name") or result.get("date") or result.get("time"))
                return result
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 파싱 실패, 원본: {content[:100]}")
                # JSON 파싱 실패 시 휴리스틱으로 폴백
                return {
                    "has_schedule_request": False,
                    "error": "JSON 파싱 실패",
                    "raw_content": content[:200]
                }
                
        except Exception as e:
            logger.error(f"일정 정보 추출 실패: {str(e)}")
            return {
                "has_schedule_request": False,
                "error": str(e)
            }

    async def generate_slot_filling_question(self, missing_fields: List[str], current_info: Dict[str, Any]) -> str:
        """누락된 정보에 대해 자연스럽게 되묻는 질문 생성"""
        try:
            field_names = {
                "date": "날짜",
                "time": "시간",
                "location": "장소",
                "friend_name": "만날 친구"
            }
            # missing_fields가 None일 경우 대비
            if not missing_fields:
                return "일정 정보를 좀 더 알려주시겠어요?"

            missing_korean = [field_names.get(f, f) for f in missing_fields]
            
            system_prompt = f"""
            당신은 사용자의 일정 비서입니다. 
            사용자가 일정을 잡으려고 하는데 다음 정보가 부족합니다: {', '.join(missing_korean)}
            
            현재 파악된 정보:
            - 날짜: {current_info.get('date') or '미정'}
            - 시간: {current_info.get('time') or '미정'}
            - 장소: {current_info.get('location') or '미정'}
            - 친구: {current_info.get('friend_name') or current_info.get('friend_names') or '미정'}
            
            사용자에게 자연스럽게 부족한 정보를 물어보세요.
            친근하고 도움이 되는 톤으로 말하세요.
            한 번에 하나씩 물어봐도 되고, 자연스럽다면 묶어서 물어봐도 됩니다.
            """
            
            # Llama API 우선 사용
            if settings.LLM_API_URL or os.getenv("LLM_API_URL"):
                return await self._call_custom_model(
                    [{"role": "system", "content": system_prompt}],
                    temperature=0.7,
                    max_tokens=150
                )
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}],
                max_tokens=150,
                temperature=0.7
            )
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"슬롯 필링 질문 생성 실패: {e}")
            # Fallback
            return f"일정을 잡으려면 {', '.join(missing_korean)} 정보가 더 필요해요. 알려주시겠어요?"
    async def generate_a2a_message(self, agent_name: str, receiver_name: str, context: str, tone: str = "polite") -> str:
        """A2A 에이전트 대화 메시지 생성"""
        try:
            system_prompt = f"""당신은 '{agent_name}'이라는 이름의 AI 비서입니다. 
상대방('{receiver_name}')의 AI 비서와 대화하며 일정을 조율하고 있습니다.

[필수 확인 시스템 팩트]: {context}
위의 시스템 팩트를 절대적으로 따르세요. 캘린더 상태와 다른 말을 지어내면 안 됩니다.

톤앤매너: {tone} (친절하고 정중하게, 하지만 간결하게)

규칙:
1. 30자 이내로 짧게 말하세요.
2. 상대방의 이름을 부르지 않아도 됩니다.
3. 이모지를 적절히 사용하세요 (1~2개).
4. 문맥에 맞는 자연스러운 한국어로 말하세요.
5. ⚠️ 반드시 순한국어만 사용! 일본어(空いている 등), 중국어, 영어 절대 금지!
6. '내 캘린더 확인 중...' 같은 기계적인 말 대신 '잠시만요, 일정 확인해볼게요!' 같이 대화하듯 말하세요.

⚠️ 절대 규칙:
- JSON 형식으로 응답하지 마세요!
- 오직 자연스러운 대화 메시지만 반환하세요.
- 예시: "좋아요! 그 시간에 뵐게요 😊"
"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "위 상황에 맞는 짧은 메시지 한 마디만 작성하세요."}
            ]
            
            # Llama API 우선 사용
            if settings.LLM_API_URL or os.getenv("LLM_API_URL"):
                result = await self._call_custom_model(messages, temperature=0.8, max_tokens=100)
                result = result.strip()
                
                # JSON 응답이 오면 자연스러운 텍스트만 추출
                if result.startswith("{"):
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict):
                            # message 필드 우선
                            if "message" in parsed and parsed["message"]:
                                result = parsed["message"]
                                logger.info(f"[Llama API] JSON.message 추출: {result[:30]}...")
                            # reason 필드 (message가 없을 때, action이 없을 때만)
                            elif "reason" in parsed and "action" not in parsed:
                                result = parsed.get("reason", "")
                                logger.info(f"[Llama API] JSON.reason 추출: {result[:30]}...")
                            else:
                                # JSON 전체인 경우 기본 메시지로 대체
                                logger.warning(f"[Llama API] JSON 응답 감지, 기본 메시지로 대체: {result[:50]}...")
                                result = "일정을 확인하고 있어요 😊"
                    except json.JSONDecodeError:
                        pass
                
                # 따옴표 제거
                result = result.strip('"').strip("'")
                logger.info(f"[Llama API] A2A 메시지 생성 완료: {result[:30]}...")
                return result

            # OpenAI 폴백
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=100,
                temperature=0.8
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"A2A 메시지 생성 실패: {str(e)}")
            # 실패 시 기본 메시지 반환 (상황에 따라 다를 수 있지만 안전하게)
            return "일정을 확인하고 있습니다."
