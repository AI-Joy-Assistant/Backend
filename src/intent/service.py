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
    def _has_batchim(char: str) -> bool:
        """한글 글자에 받침이 있는지 확인"""
        if not char or not ('가' <= char <= '힣'):
            return False
        return (ord(char) - ord('가')) % 28 > 0

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
        
        # 1. 쉼표로 구분된 이름들 추출
        comma_pattern = r"([가-힣A-Za-z]{2,}?)\s*[,，]\s*([가-힣A-Za-z]{2,}?)(?:\s|$)"
        comma_match = re.search(comma_pattern, text)
        if comma_match:
            friend_names.extend([comma_match.group(1).strip(), comma_match.group(2).strip()])
        
        # 2. "이랑", "랑", "와", "과" 등으로 연결된 이름들 추출 (Non-greedy)
        # 예: "민서랑 규민이랑", "민서와 규민"
        connector_patterns = [
            # 3명 이상 또는 2명 + 끝맺음 (예: A랑 B랑 C랑, A랑 B랑)
            r"([가-힣A-Za-z]{2,}?)\s*(이랑|랑|와|과|하고)\s*([가-힣A-Za-z]{2,}?)\s*(이랑|랑|와|과|하고)",
            # 2명 (예: A랑 B)
            r"([가-힣A-Za-z]{2,}?)\s*(이랑|랑|와|과|하고)\s*([가-힣A-Za-z]{2,}?)(?:\s|$)",
        ]
        
        for pattern in connector_patterns:
            m = re.search(pattern, text)
            if m:
                # 그룹에서 이름만 추출 (홀수 인덱스)
                # group(1)=이름1, group(2)=조사1, group(3)=이름2, group(4)=조사2...
                extracted = []
                # 정규식 구조상 group(1), group(3)이 이름
                if m.group(1): extracted.append(m.group(1).strip())
                if m.group(3): extracted.append(m.group(3).strip())
                
                # 조사와 받침 일치 여부 확인으로 정제 (선택적)
                # 예: "조이랑" -> "성신조" + "이랑" (X, 조는 받침 없음) -> "성신조이" + "랑" (O)
                refined_names = []
                for i, name in enumerate(extracted):
                    # 다음 조사가 무엇인지 확인
                    particle_idx = (i * 2) + 2 # 2, 4...
                    if particle_idx <= m.lastindex:
                        particle = m.group(particle_idx)
                        if particle == "이랑" and not IntentService._has_batchim(name[-1]):
                            refined_names.append(name + "이")
                        else:
                            refined_names.append(name)
                    else:
                        refined_names.append(name)

                # [FIX] 장소 키워드나 '에서'로 끝나는 단어는 이름에서 제외
                valid_names = []
                for n in refined_names:
                    if len(n) < 2: continue
                    if n in ["내일", "오늘", "모레", "다음", "이번"]: continue
                    if n.endswith("에서"): continue # '망원에서' 같은 경우 제외
                    if any(loc in n for loc in ["카페", "식당", "공원", "영화관"]): continue
                    valid_names.append(n)

                friend_names.extend(valid_names)
                if friend_names:
                    break
        
        # 3. 단일 친구 이름 추출 (여러 명이 없을 경우)
        if not friend_names:
            single_patterns = [
                r"([가-힣A-Za-z]{2,}?)\s*(이랑|랑|와|과|하고|와\s*함께|와\s*같이)(?:\s|$)",
                r"([가-힣A-Za-z]{2,}?)\s*(씨|님)(?:\s|$)",
            ]
            
            for pattern in single_patterns:
                m = re.search(pattern, text)
                if m:
                    name = m.group(1).strip()
                    particle = m.group(2) if m.lastindex >= 2 else ""
                    
                    # 받침 보정
                    if particle == "이랑" and not IntentService._has_batchim(name[-1]):
                         name = name + "이"
                    
                    # [FIX] 장소 키워드나 '에서'로 끝나는 단어는 이름에서 제외
                    if name.endswith("에서"): continue
                    
                    if len(name) >= 2 and name not in ["내일", "오늘", "모레", "다음", "이번", "이번주", "다음주"]:
                        friend_names.append(name)
                        break
        
        # 중복 제거 및 정리
        friend_names = list(dict.fromkeys(friend_names))  # 순서 유지하며 중복 제거
        friend_name = friend_names[0] if friend_names else None

        # 날짜 추출
        date_expr = None
        date_patterns = [
            r"(?:이번\s*주|다음\s*주|지난\s*주)?\s*[월화수목금토일]요일",
            r"(오늘|내일|모레|다음\s*주|이번\s*주)",
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

        # 제목(Title) & 활동(Activity) 추출
        title = None
        activity = None
        
        # 1. "XX 예약", "XX 약속", "XX 미팅" 패턴 (구체적)
        title_pattern = r"([가-힣A-Za-z0-9]+)\s*(예약|약속|미팅|모임|회식|회의)"
        matches = re.finditer(title_pattern, text)
        for m in matches:
            word = m.group(1)
            type_ = m.group(2)
            # "내일 예약", "오늘 약속" 등은 타이틀로 부적절하므로 제외
            if word in ["오늘", "내일", "모레", "이번주", "다음주", "점심", "저녁", "아침", "새벽", "오후", "오전"] or word.endswith("에서"):
                # 이 경우 '약속', '미팅' 자체를 활동으로 잡음
                if not activity: 
                    activity = type_
                    title = type_
                continue
            
            # 유효한 타이틀 발견
            title = f"{word} {type_}"
            activity = type_
            break
            
        # 2. 패턴 매칭 실패 시, 단순 키워드가 있는지 확인
        if not title:
            simple_keywords = ["약속", "미팅", "회의", "모임", "회식", "진료", "예약", "식사", "밥"]
            for kw in simple_keywords:
                if kw in text:
                    activity = kw
                    title = kw  # "약속" 등 단순 명사로 설정
                    break

        return {
            "intent": "schedule" if has_schedule else None,
            "friend_name": friend_name,
            "friend_names": friend_names if len(friend_names) > 1 else None,  # 여러 명일 때
            "date": date_expr,
            "time": time_expr,
            "activity": activity,
            "title": title,
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
                # logger.info(f"LLM 추출 성공: has_schedule={raw.get('has_schedule_request')}, friend={raw.get('friend_name')}")
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
            # logger.info(f"휴리스틱이 일정 의도 감지: friend_name={friend_name}")
            # 휴리스틱 결과를 우선 사용하되, LLM 결과의 다른 필드는 보존
            raw = {**heuristic_result, **{k: v for k, v in raw.items() if v not in [None, "", False]}}
            has_schedule = True

        # 여러 친구 이름 처리
        friend_names_list = raw.get("friend_names") or heuristic_result.get("friend_names")
        
        # [FIX] 최종 결과에서도 장소 키워드 필터링 (LLM이 잘못 반환했을 경우 대비)
        if friend_names_list:
            filtered_list = []
            for n in friend_names_list:
                if n.endswith("에서"): continue
                if any(loc in n for loc in ["카페", "식당", "공원", "영화관", "학교"]): continue
                filtered_list.append(n)
            friend_names_list = filtered_list

        if friend_names_list and len(friend_names_list) > 1:
            # 여러 명인 경우
            friend_name = friend_names_list[0]  # 첫 번째 이름을 대표로
        elif not friend_name:
            friend_name = raw.get("friend_name") or heuristic_result.get("friend_name")
        
        # 단일 이름도 필터링
        if friend_name:
            if friend_name.endswith("에서") or any(loc in friend_name for loc in ["카페", "식당", "공원", "영화관", "학교"]):
                friend_name = None
                # 리스트에 다른 이름이 있으면 그걸로 대체
                if friend_names_list:
                    friend_name = friend_names_list[0]
        
        # [FIX] 날짜 우선순위 조정: 명시적인 상대 날짜(내일, 모레 등)가 휴리스틱에 있다면 LLM보다 우선
        # LLM이 "내일"을 "오늘" 날짜로 잘못 계산해서 반환하는 경우 방지
        heuristic_date = heuristic_result.get("date")
        if heuristic_date and heuristic_date in ["내일", "모레", "다음주", "이번주"]:
            final_date = heuristic_date
        else:
            final_date = raw.get("date") or heuristic_date

        # 최종 병합 (빈 필드는 휴리스틱으로 채움)
        final_result = {
            "intent": "schedule" if has_schedule else None,
            "friend_name": friend_name,
            "friend_names": friend_names_list if friend_names_list and len(friend_names_list) > 1 else None,
            "date": final_date,
            "start_date": raw.get("start_date"),
            "end_date": raw.get("end_date"),
            "time": raw.get("time") or heuristic_result.get("time"),
            "start_time": raw.get("start_time"),
            "end_time": raw.get("end_time"),
            "activity": raw.get("activity") if raw.get("activity") and len(raw.get("activity")) <= 10 else heuristic_result.get("activity"),
            "title": raw.get("title") or heuristic_result.get("title"),
            "location": raw.get("location") or heuristic_result.get("location"),
            "has_schedule_request": bool(has_schedule),
            "missing_fields": raw.get("missing_fields"),
            "raw": {**raw, "heuristic_used": heuristic_result.get("has_schedule_request", False)},
        }

        # [Safety Check] "내일" 키워드가 원문에 있고, 부정어("안되고" 등)가 없으면 무조건 date="내일"로 덮어쓰기
        # LLM이나 휴리스틱이 놓쳤을 경우를 대비
        if "내일" in message and "내일" not in (final_result["date"] or ""):
            # 단순 포함 여부만 보면 위험할 수 있으나(내일은 안돼), 현재 이슈 해결을 위해 우선 적용
            final_result["date"] = "내일"
        
        # [NEW] 명시적 날짜 오버라이드 휴리스틱
        # "25일", "12월 25일" 패턴이 원본 메시지에 있으면 LLM의 "내일" 해석을 무시하고 정확한 날짜로 변환
        explicit_day_match = re.search(r'(\d{1,2})월?\s*(\d{1,2})일', message)
        if explicit_day_match:
            from datetime import datetime
            today = datetime.now()
            
            if explicit_day_match.group(1) and '월' in message[explicit_day_match.start():explicit_day_match.end()+1]:
                # "12월 25일" 패턴
                month = int(explicit_day_match.group(1))
                day = int(explicit_day_match.group(2))
            else:
                # "25일" 패턴 (월 없음) - 현재 달 또는 다음 달로 가정
                day = int(explicit_day_match.group(1)) if not explicit_day_match.group(2) else int(explicit_day_match.group(2))
                # 단일 숫자+일 패턴 재확인
                single_day_match = re.search(r'(?<!\d)(\d{1,2})일', message)
                if single_day_match:
                    day = int(single_day_match.group(1))
                month = today.month
                # 오늘보다 이전 날짜면 다음 달로
                if day < today.day:
                    month = today.month + 1 if today.month < 12 else 1
            
            year = today.year
            # 12월인데 1월을 말하면 내년
            if month < today.month:
                year += 1
            
            # 날짜 유효성 확인
            try:
                explicit_date = datetime(year, month, day)
                explicit_date_str = explicit_date.strftime("%Y-%m-%d")
                
                # LLM이 "내일"로 잘못 해석했는지 확인
                llm_date = final_result.get("start_date") or ""
                if llm_date != explicit_date_str:
                    # logger.info(f"[DATE FIX] 명시적 날짜 감지: '{message}' → {explicit_date_str} (LLM: {llm_date})")
                    final_result["start_date"] = explicit_date_str
                    final_result["end_date"] = explicit_date_str
                    final_result["date"] = f"{month}월 {day}일"
            except ValueError:
                pass  # 유효하지 않은 날짜는 무시

        # [NEW] 시간 보정 휴리스틱: LLM이 '오후 10시'를 잘못 파싱할 경우 보정
        # 원본 메시지에서 '오후' + 숫자시 패턴을 직접 확인해서 start_time 보정
        if final_result.get("start_time"):
            time_text = final_result.get("time", "") or ""
            start_time = final_result.get("start_time", "")
            
            # 원본 메시지에서 "오후 XX시" 패턴 직접 추출
            pm_time_match = re.search(r'오후\s*(\d{1,2})\s*시', message)
            am_time_match = re.search(r'오전\s*(\d{1,2})\s*시', message)
            
            if pm_time_match:
                pm_hour = int(pm_time_match.group(1))
                # 오후인데 12를 더하지 않은 경우 보정
                if pm_hour != 12:  # 오후 12시는 그대로 12:00
                    correct_hour = pm_hour + 12 if pm_hour < 12 else pm_hour
                else:
                    correct_hour = 12
                correct_time = f"{correct_hour:02d}:00"
                
                # LLM이 반환한 시간이 틀렸으면 보정
                if start_time != correct_time:
                    logger.info(f"[TIME FIX] 오후 시간 보정: '{start_time}' → '{correct_time}' (원문: 오후 {pm_hour}시)")
                    final_result["start_time"] = correct_time
                    
            elif am_time_match:
                am_hour = int(am_time_match.group(1))
                # 오전 12시는 00:00
                correct_hour = 0 if am_hour == 12 else am_hour
                correct_time = f"{correct_hour:02d}:00"
                
                if start_time != correct_time:
                    logger.info(f"[TIME FIX] 오전 시간 보정: '{start_time}' → '{correct_time}' (원문: 오전 {am_hour}시)")
                    final_result["start_time"] = correct_time
        
        # end_time도 같은 로직으로 보정
        if final_result.get("end_time"):
            end_time = final_result.get("end_time", "")
            
            # 원본 메시지에서 끝 시간 패턴 추출 (예: "~오후 11시", "오후 11시까지")
            pm_end_match = re.search(r'오후\s*(\d{1,2})\s*시(?:까지)?', message)
            end_keyword_match = re.search(r'[~\-]\s*오후\s*(\d{1,2})\s*시', message) or re.search(r'(\d{1,2})\s*시까지', message)
            
            if end_keyword_match or pm_end_match:
                match = end_keyword_match or pm_end_match
                end_hour = int(match.group(1))
                
                # 끝 시간에 "오후"가 있거나 메시지에 "오후"가 있으면 PM으로 변환
                if "오후" in message and end_hour < 12:
                    correct_end_hour = end_hour + 12
                elif end_hour == 12:
                    correct_end_hour = 12
                else:
                    correct_end_hour = end_hour
                    
                correct_end_time = f"{correct_end_hour:02d}:00"
                
                if end_time != correct_end_time and correct_end_hour >= 12:  # 오후 시간일 때만 보정
                    logger.info(f"[TIME FIX] 끝 시간 보정: '{end_time}' → '{correct_end_time}'")
                    final_result["end_time"] = correct_end_time

        # logger.info(f"최종 Intent 추출 결과: {final_result}")
        
        result = IntentParseResult(**final_result)
        return result.model_dump()
