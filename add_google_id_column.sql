-- user 테이블에 google_id 컬럼 추가
ALTER TABLE "user" ADD COLUMN google_id VARCHAR(255);

-- google_id에 인덱스 추가 (선택사항)
CREATE INDEX IF NOT EXISTS idx_user_google_id ON "user"(google_id);

-- 기존 컬럼 구조 확인
SELECT column_name, data_type, is_nullable 
FROM information_schema.columns 
WHERE table_name = 'user' 
ORDER BY ordinal_position; 