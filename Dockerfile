FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 복사
COPY . .

# 포트 설정 (Cloud Run은 PORT 환경변수 사용)
EXPOSE 8080

# 실행 명령 (PORT 환경변수 사용)
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
