const axios = require('axios');
const jwt = require('jsonwebtoken');
const { findUserByEmail, createUser, updateUserStatus } = require('./auth.repository');
require('dotenv').config();

// [1] 구글 로그인 URL 생성
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

// [2] 구글로부터 access_token + refresh_token 받기
exports.getGoogleUserInfo = async (code) => {
    // (1) access token 요청
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
    const accessToken = access_token;
    const refreshToken = refresh_token;
    const expiresIn = expires_in;

    // (2) access token으로 사용자 정보 조회
    const userInfoRes = await axios.get('https://www.googleapis.com/oauth2/v3/userinfo', {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    const { email, name, picture } = userInfoRes.data;

    // (3) DB에 사용자 저장 (있으면 패스, 없으면 create)
    let user = await findUserByEmail(email);
    if (!user) {
        user = await createUser({
            email,
            name,
            login_provider: 'google',
        });
    }
    await updateUserStatus(email, 'ONLINE');

    return {
        accessToken,
        refreshToken,
        expiresIn,
        user: { email, name, picture },
    };
};

exports.getNewAccessTokenFromGoogle = async (refreshToken) => {
    try {
        const res = await axios.post('https://oauth2.googleapis.com/token', null, {
            params: {
                client_id: process.env.GOOGLE_CLIENT_ID,
                client_secret: process.env.GOOGLE_CLIENT_SECRET,
                refresh_token: refreshToken,
                grant_type: 'refresh_token',
            },
        });

        const { access_token, expires_in } = res.data;

        return { accessToken: access_token, expiresIn: expires_in };
    } catch (error) {
        console.error('[Google Refresh Error]', error.message);
        throw new Error('구글 accessToken 재발급 실패');
    }
};

exports.fetchUserInfoFromGoogle = async (accessToken) => {
    const res = await axios.get('https://www.googleapis.com/oauth2/v3/userinfo', {
        headers: {
            Authorization: `Bearer ${accessToken}`,
        },
    });

    return res.data;
};
