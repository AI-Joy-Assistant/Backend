const supabase = require('../../config/db.config'); // 기존 db.config.js는 이제 supabase 클라이언트!

// 사용자 이메일로 조회
exports.findUserByEmail = async (email) => {
    const { data, error } = await supabase
        .from('users')
        .select('*')
        .eq('email', email)
        .maybeSingle();

    if (error) throw error;
    return data;
};

// 사용자 생성
exports.createUser = async ({ email, name, login_provider }) => {
    const { data, error } = await supabase
        .from('users')
        .insert([{ email, name, login_provider }])
        .single();

    if (error) throw error;
    return data;
};

// 사용자 상태 업데이트 (ONLINE / OFFLINE)
exports.updateUserStatus = async (email, status) => {
    const { error } = await supabase
        .from('users')
        .update({ status })
        .eq('email', email);

    if (error) throw error;
};

// refresh token으로 사용자 조회
exports.findByRefreshToken = async (refreshToken) => {
    const { data, error } = await supabase
        .from('users')
        .select('*')
        .eq('refresh_token', refreshToken)
        .single();

    if (error) throw error;
    return data;
};

// refresh token 저장
exports.updateRefreshToken = async (userId, refreshToken) => {
    console.log('[DEBUG] updateRefreshToken:', { userId, refreshToken });
    const { error } = await supabase
        .from('users')
        .update({ refresh_token: refreshToken })
        .eq('id', userId);

    if (error) throw error;
};

// refresh token 제거 (로그아웃)
exports.clearRefreshToken = async (userId) => {
    const { error } = await supabase
        .from('users')
        .update({ refresh_token: null })
        .eq('id', userId);

    if (error) throw error;
};
