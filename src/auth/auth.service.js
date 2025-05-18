const axios = require('axios');
const { findUserByEmail, createUser, updateUserStatus, updateRefreshToken, clearRefreshToken } = require('./auth.repository');
require('dotenv').config();

exports.getGoogleAuthURL = () => {
    const rootUrl = 'https://accounts.google.com/o/oauth2/v2/auth';
    const options = new URLSearchParams({
        redirect_uri: process.env.GOOGLE_REDIRECT_URI,
        client_id: process.env.GOOGLE_CLIENT_ID,
        access_type: 'offline',
        response_type: 'code',
        prompt: 'consent',
        scope: 'openid email profile',
    });
    return `${rootUrl}?${options.toString()}`;
};

exports.handleGoogleCallback = async (code) => {
    try {
        // 1. 토큰 요청
        const tokenRes = await axios.post('https://oauth2.googleapis.com/token', null, {
            params: {
                code,
                client_id: process.env.GOOGLE_CLIENT_ID,
                client_secret: process.env.GOOGLE_CLIENT_SECRET,
                redirect_uri: process.env.GOOGLE_REDIRECT_URI,
                grant_type: 'authorization_code',
            },
        });

        const { access_token, refresh_token, expires_in } = tokenRes.data;

        // 2. 유저 정보 요청
        const userInfoRes = await axios.get('https://www.googleapis.com/oauth2/v3/userinfo', {
            headers: { Authorization: `Bearer ${access_token}` },
        });
        const { email, name, picture } = userInfoRes.data;

        // 3. 유저 DB 처리
        let user = await findUserByEmail(email);
        if (!user) {
            user = await createUser({ email, name, login_provider: 'google' });
        }
        await updateUserStatus(email, 'ONLINE');
        if (refresh_token) {
            await updateRefreshToken(user.id, refresh_token);
        }

        return {
            refreshToken: refresh_token,
            response: {
                message: `환영합니다, ${name}님`,
                accessToken: access_token,
                expiresIn: expires_in,
                user: { email, name, picture },
            },
        };
    } catch (err) {
        console.error('구글 로그인 실패:', err.message);
        throw new Error('로그인 실패');
    }
};

exports.getNewAccessTokenFromGoogle = async (refreshToken) => {
    if (!refreshToken) {
        return { status: 401, body: { message: 'Refresh Token이 없습니다.' } };
    }

    try {
        const res = await axios.post('https://oauth2.googleapis.com/token', null, {
            params: {
                client_id: process.env.GOOGLE_CLIENT_ID,
                client_secret: process.env.GOOGLE_CLIENT_SECRET,
                refresh_token: refreshToken,
                grant_type: 'refresh_token',
            },
        });

        return {
            status: 200,
            body: {
                accessToken: res.data.access_token,
                expiresIn: res.data.expires_in,
            },
        };
    } catch (err) {
        return {
            status: 500,
            body: { message: err.message || 'accessToken 재발급 실패' },
        };
    }
};

exports.handleLogout = async (email) => {
    if (!email) {
        return { status: 400, message: '유저 이메일이 없습니다.' };
    }

    const user = await findUserByEmail(email);
    if (user) {
        await updateUserStatus(email, 'OFFLINE');
        await clearRefreshToken(user.id);
    }

    return { status: 200, message: '로그아웃 완료' };
};

exports.fetchUserInfoFromGoogle = async (accessToken) => {
    if (!accessToken) {
        return { status: 401, body: { message: 'Access Token이 없습니다.' } };
    }

    try {
        const res = await axios.get('https://www.googleapis.com/oauth2/v3/userinfo', {
            headers: { Authorization: `Bearer ${accessToken}` },
        });

        return { status: 200, body: res.data };
    } catch (err) {
        return { status: 401, body: { message: '유효하지 않은 accessToken입니다.' } };
    }
};
