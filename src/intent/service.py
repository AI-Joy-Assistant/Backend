from typing import Dict, Any
import logging
import re

from .models import IntentParseResult
from src.chat.chat_openai_service import OpenAIService

logger = logging.getLogger(__name__)


class IntentService:
    """
    Intent 전담 서비스. LLM 기반/규칙 기반 감지를 여기서만 수행하고
    Chat/A2A/Calendar 등 상위 흐름에서는 결과만 소비하도록 분리한다.
    """

    @staticmethod
    def _heuristic_parse(message: str) -> Dict[str, Any]:
        """
        LLM 실패 시를 대비한 강화된 휴리스틱 파서.
        - 일정/약속 관련 키워드로 intent 추정
        - 다양한 형태의 친구 이름 추출
        """
        text = (message or "").strip()
        lowered = text.lower()

        # 일정 관련 키워드 확장
        schedule_keywords = [
            "일정", "약속", "잡아줘", "스케줄", "잡아", "잡기", "잡아줄래", "잡아줘.",
            "만나", "만날", "만나자", "만나요", "만나고", "만나서",
            "약속잡", "약속 잡", "일정잡", "일정 잡",
            "시간", "시간대", "언제", "몇시"
        ]
        has_schedule = any(k in text for k in schedule_keywords) or any(k in lowered for k in ["schedule", "meeting", "appointment"])

        friend_names = []
        # 여러 친구 이름 추출 (쉼표, "이랑", "랑", "와" 등으로 구분)
        # 예: "민서, 규민이랑", "민서와 규민", "민서랑 규민이랑"
        
        # 먼저 쉼표로 구분된 이름들 추출
        comma_pattern = r"([가-힣A-Za-z]{2,})\s*[,，]\s*([가-힣A-Za-z]{2,})"
        comma_match = re.search(comma_pattern, text)
        if comma_match:
            friend_names.extend([comma_match.group(1).strip(), comma_match.group(2).strip()])
        
        # "이랑", "랑", "와", "과" 등으로 연결된 이름들 추출
        connector_patterns = [
            r"([가-힣A-Za-z]{2,})\s*(이랑|랑|와|과|하고)\s*([가-힣A-Za-z]{2,})",  # "민서랑 규민"
            r"([가-힣A-Za-z]{2,})\s*(이랑|랑|와|과|하고)\s*([가-힣A-Za-z]{2,})\s*(이랑|랑|와|과|하고)",  # "민서랑 규민이랑"
        ]
        
        for pattern in connector_patterns:
            m = re.search(pattern, text)
            if m:
                names = [m.group(1).strip(), m.group(3).strip()]
                friend_names.extend([n for n in names if len(n) >= 2 and n not in ["내일", "오늘", "모레", "다음", "이번"]])
                if friend_names:
                    break
        
        # 단일 친구 이름 추출 (여러 명이 없을 경우)
        if not friend_names:
            # 더 정확한 패턴: 이름 뒤에 오는 접미사를 명확히 구분
            # "성신조이랑" 같은 경우 "성신조이" 전체를 매칭하도록 수정
            single_patterns = [
                r"([가-힣A-Za-z]{2,}이)\s*(랑|와|과|하고)",  # "성신조이랑", "민서이랑" (이름이 "이"로 끝나는 경우)
                r"([가-힣A-Za-z]{2,})\s*(씨|님|이랑|랑|하고|과|와|와\s*함께|와\s*같이)",  # "민서랑", "민서와 함께"
                r"([가-힣A-Za-z]{2,})\s*하고\s*",  # "민서하고"
                r"([가-힣A-Za-z]{2,})\s*와\s*",  # "민서와"
                r"([가-힣A-Za-z]{2,})\s*랑\s*",  # "민서랑"
                r"([가-힣A-Za-z]{2,})\s*과\s*",  # "민서과"
                r"([가-힣A-Za-z]{2,})\s*님",  # "민서님"
                r"([가-힣A-Za-z]{2,})\s*씨",  # "민서씨"
            ]
            
            for pattern in single_patterns:
                m = re.search(pattern, text)
                if m:
                    name = m.group(1).strip()
                    # 최소 2글자 이상이고, 일반 단어가 아닌 경우만 추가
                    if len(name) >= 2 and name not in ["내일", "오늘", "모레", "다음", "이번", "이번주", "다음주"]:
                        friend_names.append(name)
                        break
        
        # 중복 제거 및 정리
        friend_names = list(dict.fromkeys(friend_names))  # 순서 유지하며 중복 제거
        friend_name = friend_names[0] if friend_names else None

        # 날짜 추출
        date_expr = None
        date_patterns = [
            r"(오늘|내일|모레|다음주|이번주)",
            r"(\d{1,2})\s*월\s*(\d{1,2})\s*일",
            r"(\d{1,2})\s*일",
        ]
        for pattern in date_patterns:
            m = re.search(pattern, text)
            if m:
                date_expr = m.group(0)
                break

        # 시간 표현 추출 (오전/오후 HH시)
        time_expr = None
        time_patterns = [
            r"(오전|오후)\s*(\d{1,2})\s*시",
            r"(\d{1,2})\s*시",
            r"(점심|저녁|아침|새벽|낮)",
        ]
        for pattern in time_patterns:
            m = re.search(pattern, text)
            if m:
                time_expr = m.group(0).replace(" ", "")
                break

        # 장소 추출
        location = None
        location_keywords = ["에서", "장소", "카페", "식당", "레스토랑", "공원", "영화관"]
        for keyword in location_keywords:
            if keyword in text:
                # 키워드 주변 텍스트 추출
                idx = text.find(keyword)
                if idx > 0:
                    # 키워드 앞의 5글자 정도 추출
                    start = max(0, idx - 10)
                    location_candidate = text[start:idx + len(keyword)]
                    # 의미있는 단어만 추출
                    words = re.findall(r"[가-힣A-Za-z]+", location_candidate)
                    if words:
                        location = words[-1] if len(words[-1]) > 1 else None
                break

        return {
            "intent": "schedule" if has_schedule else None,
            "friend_name": friend_name,
            "friend_names": friend_names if len(friend_names) > 1 else None,  # 여러 명일 때
            "date": date_expr,
            "time": time_expr,
            "activity": "약속" if has_schedule else None,
            "location": location,
            "has_schedule_request": has_schedule,
            "raw": {"heuristic": True},
        }

    @staticmethod
    async def extract_schedule_info(message: str) -> Dict[str, Any]:
        """
        일정 관련 인텐트/엔티티를 추출한다.
        LLM과 휴리스틱을 결합하여 더 정확한 추출을 수행.
        """
        # 먼저 휴리스틱으로 빠르게 파싱
        heuristic_result = IntentService._heuristic_parse(message)
        
        # LLM 호출 시도
        raw = {}
        try:
            openai_service = OpenAIService()
            llm_result = await openai_service.extract_schedule_info(message)
            
            # LLM 결과가 유효한지 확인
            if isinstance(llm_result, dict) and "has_schedule_request" in llm_result:
                raw = llm_result
                logger.info(f"LLM 추출 성공: has_schedule={raw.get('has_schedule_request')}, friend={raw.get('friend_name')}")
            else:
                logger.warning(f"LLM 결과 형식 오류, 휴리스틱 사용: {llm_result}")
        except Exception as e:
            logger.warning(f"Intent LLM 호출 실패, 휴리스틱으로 대체: {e}")
            raw = {}

        # LLM과 휴리스틱 결과 병합 (휴리스틱이 더 확실한 경우 우선)
        has_schedule = raw.get("has_schedule_request") or heuristic_result.get("has_schedule_request", False)
        friend_name = raw.get("friend_name") or heuristic_result.get("friend_name")
        
        # LLM이 일정 의도를 못 잡았지만 휴리스틱이 잡은 경우
        if not raw.get("has_schedule_request") and heuristic_result.get("has_schedule_request"):
            logger.info(f"휴리스틱이 일정 의도 감지: friend_name={friend_name}")
            # 휴리스틱 결과를 우선 사용하되, LLM 결과의 다른 필드는 보존
            raw = {**heuristic_result, **{k: v for k, v in raw.items() if v not in [None, "", False]}}
            has_schedule = True

        # 여러 친구 이름 처리
        friend_names_list = raw.get("friend_names") or heuristic_result.get("friend_names")
        if friend_names_list and len(friend_names_list) > 1:
            # 여러 명인 경우
            friend_name = friend_names_list[0]  # 첫 번째 이름을 대표로
        elif not friend_name:
            friend_name = raw.get("friend_name") or heuristic_result.get("friend_name")
        
        # 최종 병합 (빈 필드는 휴리스틱으로 채움)
        final_result = {
            "intent": "schedule" if has_schedule else None,
            "friend_name": friend_name,
            "friend_names": friend_names_list if friend_names_list and len(friend_names_list) > 1 else None,
            "date": raw.get("date") or heuristic_result.get("date"),
            "time": raw.get("time") or heuristic_result.get("time"),
            "activity": raw.get("activity") or heuristic_result.get("activity"),
            "location": raw.get("location") or heuristic_result.get("location"),
            "has_schedule_request": bool(has_schedule),
            "raw": {**raw, "heuristic_used": heuristic_result.get("has_schedule_request", False)},
        }

        logger.info(f"최종 Intent 추출 결과: {final_result}")
        
        result = IntentParseResult(**final_result)
        return result.model_dump()
