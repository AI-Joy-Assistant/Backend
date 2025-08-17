# OpenAI API 설정 가이드

## 1. OpenAI API 키 발급

1. [OpenAI 웹사이트](https://platform.openai.com/)에 접속
2. 계정 생성 또는 로그인
3. API Keys 섹션으로 이동
4. "Create new secret key" 클릭
5. API 키를 안전한 곳에 복사해두기

## 2. 환경 변수 설정

`.env` 파일에 다음 내용을 추가하세요:

### 모델 선택 가이드
- **일반적인 대화**: `OPENAI_MODEL=gpt-3.5-turbo` (가장 경제적)
- **복잡한 일정 조율**: `OPENAI_MODEL=gpt-4` (가장 정확함)
- **빠른 응답 필요**: `OPENAI_MODEL=gpt-4-turbo` (빠르고 정확함)
- **멀티모달 기능**: `OPENAI_MODEL=gpt-4o` (이미지 처리 가능)
- **비용 효율성**: `OPENAI_MODEL=gpt-4o-mini` (GPT-4 성능, 저렴한 비용)

```env
# OpenAI 설정
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4  # gpt-4, gpt-4-turbo, gpt-4o, gpt-4o-mini 중 선택
```

## 3. API 사용량 및 비용

### GPT 모델별 비용 (2024년 기준)
- **GPT-3.5-turbo**: $0.0015 / 1K input tokens, $0.002 / 1K output tokens
- **GPT-4**: $0.03 / 1K input tokens, $0.06 / 1K output tokens
- **GPT-4-turbo**: $0.01 / 1K input tokens, $0.03 / 1K output tokens
- **GPT-4o**: $0.005 / 1K input tokens, $0.015 / 1K output tokens
- **GPT-4o-mini**: $0.00015 / 1K input tokens, $0.0006 / 1K output tokens

### 모델별 특징
- **GPT-3.5-turbo**: 빠르고 경제적, 일반적인 대화에 적합
- **GPT-4**: 가장 강력한 추론 능력, 복잡한 작업에 적합
- **GPT-4-turbo**: GPT-4의 개선된 버전, 더 빠른 응답
- **GPT-4o**: 멀티모달 지원, 이미지/텍스트 동시 처리
- **GPT-4o-mini**: GPT-4o의 경량 버전, 비용 효율적

- **무료 크레딧**: 신규 사용자에게 $5 크레딧 제공
- **사용량 모니터링**: [OpenAI Usage Dashboard](https://platform.openai.com/usage)에서 확인 가능

## 4. 새로운 API 엔드포인트

### AI 대화 시작
```
POST /chat/start-ai-session
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
    "message": "아구만이랑 내일 점심 약속 잡아줘"
}
```

### ChatGPT와 자유 대화
```
POST /chat/chat
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
    "message": "안녕하세요! 오늘 날씨는 어때요?"
}
```

## 5. 응답 형식

```json
{
    "user_message": "아구만이랑 내일 점심 약속 잡아줘",
    "ai_response": "네! 아구만님과 내일 점심 약속을 잡아드리겠습니다...",
    "schedule_info": {
        "friend_name": "아구만",
        "date": "내일",
        "time": "점심",
        "activity": "약속",
        "has_schedule_request": true
    },
    "usage": {
        "prompt_tokens": 150,
        "completion_tokens": 200,
        "total_tokens": 350
    }
}
```

## 6. 주의사항

1. **API 키 보안**: API 키를 절대 코드에 하드코딩하지 마세요
2. **사용량 제한**: OpenAI API는 요청당 토큰 수 제한이 있습니다
3. **에러 처리**: 네트워크 오류나 API 제한에 대한 적절한 에러 처리가 필요합니다
4. **비용 관리**: 사용량을 모니터링하여 예상치 못한 비용이 발생하지 않도록 주의하세요

## 7. 테스트

API 키 설정 후 다음 명령으로 테스트할 수 있습니다:

```bash
curl -X POST "http://localhost:3000/chat/chat" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your_jwt_token>" \
  -d '{"message": "안녕하세요!"}'
```
