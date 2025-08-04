-- 기존 테이블들이 이미 존재하므로 인덱스와 정책만 추가

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_friend_follow_request_id ON friend_follow(request_id);
CREATE INDEX IF NOT EXISTS idx_friend_follow_receiver_id ON friend_follow(receiver_id);
CREATE INDEX IF NOT EXISTS idx_friend_follow_status ON friend_follow(follow_status);
CREATE INDEX IF NOT EXISTS idx_friend_list_user_id ON friend_list(user_id);
CREATE INDEX IF NOT EXISTS idx_friend_list_friend_id ON friend_list(friend_id);
CREATE INDEX IF NOT EXISTS idx_friend_list_status ON friend_list(status);

-- RLS (Row Level Security) 정책
ALTER TABLE friend_follow ENABLE ROW LEVEL SECURITY;
ALTER TABLE friend_list ENABLE ROW LEVEL SECURITY;

-- 친구 요청 테이블 정책
CREATE POLICY "Users can view friend requests they sent or received" ON friend_follow
    FOR SELECT USING (auth.uid() = request_id OR auth.uid() = receiver_id);

CREATE POLICY "Users can insert friend requests" ON friend_follow
    FOR INSERT WITH CHECK (auth.uid() = request_id);

CREATE POLICY "Users can update friend requests they received" ON friend_follow
    FOR UPDATE USING (auth.uid() = receiver_id);

-- 친구 테이블 정책
CREATE POLICY "Users can view their friends" ON friend_list
    FOR SELECT USING (auth.uid() = user_id OR auth.uid() = friend_id);

CREATE POLICY "Users can insert friend relationships" ON friend_list
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their friend relationships" ON friend_list
    FOR UPDATE USING (auth.uid() = user_id OR auth.uid() = friend_id);

-- 트리거 함수 (updated_at 자동 업데이트)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 트리거 생성
CREATE TRIGGER update_friend_list_updated_at 
    BEFORE UPDATE ON friend_list 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column(); 