const Database = require('../../config/db.config');
const db = new Database();

exports.findUserByEmail = async (email) => {
    const query = 'SELECT * FROM users WHERE email = $1 LIMIT 1';
    const result = await db.query(query, [email]);
    return result.rows[0] || null;
};

exports.createUser = async ({ email, name, login_provider }) => {
    const query = `
    INSERT INTO users (email, name, login_provider)
    VALUES ($1, $2, $3)
    RETURNING *`;
    const result = await db.query(query, [email, name, login_provider]);
    return result.rows[0];
};

exports.updateUserStatus = async (email, status) => {
    await db.query(
        'UPDATE users SET status = $1 WHERE email = $2',
        [status, email]
    );
};

//  refresh token으로 사용자 조회
exports.findByRefreshToken = async (refreshToken) => {
    const query = 'SELECT * FROM users WHERE refresh_token = $1 LIMIT 1';
    const result = await db.query(query, [refreshToken]);
    return result.rows[0] || null;
};

//  refresh token 저장
exports.updateRefreshToken = async (userId, refreshToken) => {
    const query = 'UPDATE users SET refresh_token = $1 WHERE id = $2';
    await db.query(query, [refreshToken, userId]);
};


//  refresh token 제거 (로그아웃)
exports.clearRefreshToken = async (userId) => {
    const query = 'UPDATE users SET refresh_token = NULL WHERE id = $1';
    await db.query(query, [userId]);
};
