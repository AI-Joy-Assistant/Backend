import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from config.settings import settings

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
    
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
            
            system_prompt = f"""당신은 AI Joy Assistant의 일정 조율 도우미입니다. 
사용자와 친구들의 일정을 조율하고 약속을 잡는 것을 도와주세요.

현재 시간: {current_time}

주요 기능:
1. 친구와의 일정 조율
2. 약속 시간 및 장소 제안
3. 일정 충돌 확인
4. 친근하고 도움이 되는 대화

## ⚠️ 절대 규칙: 정보를 만들어내지 마세요!

**사용자가 말하지 않은 정보는 절대 추가하지 마세요!**
- 사용자가 장소를 말하지 않았으면 장소를 추측하거나 만들어내지 마세요
- "강남", "홍대" 등 구체적인 장소명을 사용자가 말하지 않았으면 사용하지 마세요
- 사용자가 말한 내용만 정확하게 사용하세요

## ⚠️ 짧은 응답 해석 규칙

사용자가 짧게 답하면 대화 맥락을 보고 해석하세요:
- "아닝", "아니", "몰라", "미정" = 끝나는 시간이 정해지지 않음 → 시작 시간만으로 등록
- "응", "네", "그래" = 확인/동의
- **"아닝"을 "안녕"으로 해석하지 마세요!** 이것은 인사가 아니라 "아니"의 줄임말입니다.

## ⚠️ 가장 중요한 규칙: 대화 맥락 기억

**이전 대화 내용을 반드시 기억하세요!** 사용자가 이미 말한 정보를 절대 다시 물어보지 마세요.

일정 등록 대화 중에 짧은 응답이 오면, 그것은 대화의 연속입니다. 새로운 대화가 아닙니다!

예시:
- 사용자가 "서점에 가야돼"라고 했으면, 일정 내용은 이미 "서점 방문"입니다.
- 사용자가 "2시에"라고 했으면, 이전 대화의 일정 시간입니다.
- 사용자가 "아닝"이라고 했으면, 끝나는 시간이 없다는 의미입니다. 바로 등록하세요!

## 일정 등록 요청 시:

### 필수 정보 확인 순서:
1. **일정 내용** - 이미 대화에서 언급되었는지 확인!
2. **날짜** - 이미 언급되었는지 확인
3. **시간** - 없으면 "몇 시에 가실 예정인가요?"

### 시간 정보 확인 (매우 중요):
- **시간 정보가 아예 없으면**: "몇 시에 가실 예정인가요?"
- **시작 시간만 말했을 때**: 절대 "등록했습니다"라고 하지 말고, **"끝나는 시간도 정해졌나요?"** 라고 먼저 물어보세요.
- **"아닝", "아니", "몰라", "미정"이면**: 그때 비로소 "네, 오후 00시로 등록했습니다!"라고 완료 메시지를 보내세요.

### 올바른 대화 예시:
```
사용자: "내일 동생 데리기 일정 등록해줘"
AI: "네, 내일 동생 데리기 일정을 등록해드릴게요. 몇 시에 가실 예정인가요?"

사용자: "2시에 갈거야"
AI: "알겠습니다! 내일 오후 2시에 '동생 데리기' 일정을 등록할게요. 끝나는 시간도 정해졌나요?"

사용자: "아닝"
AI: "네, 내일 오후 2시 '동생 데리기' 일정으로 등록했습니다! ✅"
```

시간 관련 질문에 답할 때는 현재 시간을 참고하여 정확한 답변을 제공하세요.
요일 계산이 필요한 경우 현재 요일을 기준으로 정확히 계산하세요.
항상 친근하고 도움이 되는 톤으로 응답하세요."""

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
            
            user_msg = "죄송해요, 지금 잠시 생각이 안 나네요. 🤯 잠시 후 다시 말해주시겠어요?"
            if "insufficient_quota" in error_msg:
                user_msg = "API 사용량 한도가 초과되었어요. 관리자에게 문의해주세요. 🥲"
            elif "rate limit" in error_msg.lower():
                user_msg = "지금 너무 많은 대화가 오고 가고 있어요. 잠시만 기다려주세요! 🕒"
                
            return {
                "status": "error",
                "message": user_msg,
                "error": error_msg
            }
    
    async def extract_schedule_info(self, message: str) -> Dict[str, Any]:
        """메시지에서 일정 관련 정보 추출"""
        try:
            current_time = self._get_current_time_info()
            
            system_prompt = f"""다음 메시지에서 일정 관련 정보를 추출해주세요.
현재 시간: {current_time}

**중요: 반드시 유효한 JSON만 반환하세요. 설명이나 추가 텍스트 없이 JSON만 반환하세요.**

JSON 형태로 다음 정보를 반환하세요:
{{
    "friend_name": "친구 이름 (1명인 경우)",
    "friend_names": ["친구1", "친구2"] (여러 명인 경우, friend_name보다 우선),
    "date": "날짜 (오늘, 내일, 모레, 특정 날짜, 이번주 등)",
    "time": "시간 (점심, 저녁, 특정 시간) 또는 null (시간 정보가 없을 때)",
    "end_time": "종료 시간 (있다면) 또는 null",
    "activity": "활동 내용 (밥, 미팅, 진료 등)",
    "title": "일정 제목 - 구체적인 장소나 목적을 포함 (예: 병원 방문, 치과 예약, 팀 미팅)",
    "location": "장소 (있다면)",
    "has_schedule_request": true 또는 false,
    "time_specified": true 또는 false (사용자가 시간을 명시했는지 여부)
}}

## 일정 제목(title) 추출 규칙:
- 장소가 포함된 경우: "병원", "치과", "학교", "회사" 등 → "병원 방문", "치과 진료", "학교 수업" 등
- 활동이 포함된 경우: "밥", "저녁", "미팅" 등 → "저녁 식사", "점심 약속", "팀 미팅" 등
- 구체적인 내용 우선: "치과 예약이 있어" → title: "치과 예약"
- 일반적인 표현: "가야해", "갈거야" → activity로 추론하여 title 생성

## 예시:
- "내일 병원에 갈거야 일정 등록해줘" → {{"date": "내일", "time": null, "title": "병원 방문", "activity": "병원", "has_schedule_request": true, "time_specified": false}}
- "2시에 갈거야" (이전 맥락: 병원) → {{"time": "2시", "title": "병원 방문", "has_schedule_request": true, "time_specified": true}}
- "병원간다니까" → {{"title": "병원 방문", "activity": "병원", "has_schedule_request": true}}
- "아구만이랑 내일 점심 약속 잡아줘" → {{"friend_name": "아구만", "date": "내일", "time": "점심", "activity": "약속", "title": "점심 약속", "has_schedule_request": true, "time_specified": true}}
- "민서, 규민이랑 이번주 금요일 저녁 7시에 밥 약속 잡아줘" → {{"friend_names": ["민서", "규민"], "date": "이번주 금요일", "time": "저녁 7시", "activity": "밥", "title": "저녁 식사", "has_schedule_request": true, "time_specified": true}}
- "내일 치과 예약이 있어 3시에 일정 등록해줘" → {{"date": "내일", "time": "3시", "title": "치과 예약", "has_schedule_request": true, "time_specified": true}}
- "안녕하세요" → {{"has_schedule_request": false}}

**반드시 JSON 형식만 반환하세요. 다른 텍스트는 포함하지 마세요.**"""

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                max_tokens=200,
                temperature=0.1
            )
            
            try:
                content = response.choices[0].message.content.strip()
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
                logger.warning(f"JSON 파싱 실패, 원본: {response.choices[0].message.content[:100]}")
                # JSON 파싱 실패 시 휴리스틱으로 폴백
                return {
                    "has_schedule_request": False,
                    "error": "JSON 파싱 실패",
                    "raw_content": response.choices[0].message.content[:200]
                }
                
        except Exception as e:
            logger.error(f"일정 정보 추출 실패: {str(e)}")
            return {
                "has_schedule_request": False,
                "error": str(e)
            }
    async def generate_a2a_message(self, agent_name: str, receiver_name: str, context: str, tone: str = "polite") -> str:
        """A2A 에이전트 대화 메시지 생성"""
        try:
            system_prompt = f"""당신은 '{agent_name}'이라는 이름의 AI 비서입니다. 
상대방('{receiver_name}')의 AI 비서와 대화하며 일정을 조율하고 있습니다.

상황: {context}
톤앤매너: {tone} (친절하고 정중하게, 하지만 간결하게)

규칙:
1. 30자 이내로 짧게 말하세요.
2. 상대방의 이름을 부르지 않아도 됩니다.
3. 이모지를 적절히 사용하세요 (1~2개).
4. 문맥에 맞는 자연스러운 한국어로 말하세요.
5. '내 캘린더 확인 중...' 같은 기계적인 말 대신 '잠시만요, 일정 확인해볼게요!' 같이 대화하듯 말하세요.
"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "이 상황에 맞는 한 마디를 해주세요."}
            ]
            
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
