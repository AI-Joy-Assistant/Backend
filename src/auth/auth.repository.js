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