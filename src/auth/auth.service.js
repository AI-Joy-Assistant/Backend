const axios = require('axios');
const jwt = require('jsonwebtoken');
const { findUserByEmail, createUser, updateUserStatus, updateRefreshToken, clearRefreshToken } = require('./auth.repository');
require('dotenv').config();

function createJwtAccessToken(user) {
    return jwt.sign(
        { id: user.id, email: user.email },
        process.env.JWT_SECRET,
        { expiresIn: '1h' }
    );
}

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

        // 4. JWT access token 발급
        const jwtAccessToken = createJwtAccessToken(user);

        return {
            refreshToken: refresh_token,
            response: {
                message: `환영합니다, ${name}님`,
                accessToken: jwtAccessToken,
                expiresIn: 3600,
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
        const googleAccessToken = res.data.access_token;

        // (2) 이 토큰으로 유저 정보 조회
        const userInfoRes = await axios.get('https://www.googleapis.com/oauth2/v3/userinfo', {
            headers: { Authorization: `Bearer ${googleAccessToken}` },
        });
        const { email } = userInfoRes.data;

        const user = await findUserByEmail(email);
        if (!user) {
            return { status: 404, body: { message: '해당 사용자를 찾을 수 없습니다.' } };
        }

        // (3) 자체 JWT accessToken 발급
        const jwtAccessToken = jwt.sign(
            { id: user.id, email: user.email },
            process.env.JWT_SECRET,
            { expiresIn: '1h' }
        );

        return {
            status: 200,
            body: {
                accessToken: jwtAccessToken,
                expiresIn: 3600,
            },
        };
    } catch (err) {
        return {
            status: 500,
            body: { message: err.message || 'accessToken 재발급 실패' },
        };
    }
};

exports.handleLogout = async (token) => {
    // if (!email) {
    //     return { status: 400, message: '유저 이메일이 없습니다.' };
    // }
    //
    // const user = await findUserByEmail(email);
    // if (user) {
    //     await updateUserStatus(email, 'OFFLINE');
    //     await clearRefreshToken(user.id);
    // }
    //
    // return { status: 200, message: '로그아웃 완료' };
    if (!token) {
        return { status: 401, message: 'Access Token이 없습니다.' };
    }
    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);
        const email = decoded.email;

        const user = await findUserByEmail(email);
        if (!user) {
            return { status: 404, message: '해당 사용자를 찾을 수 없습니다.' };
        }
        await updateUserStatus(email, 'OFFLINE');
        await clearRefreshToken(user.id);

        return { status: 200, message: '로그아웃 완료' };
    } catch (err) {
        return { status: 401, message: '유효하지 않은 Access Token입니다.' };
    }

};

exports.fetchUserInfoFromGoogle = async (token) => {
    if (!token) {
        return {
            status: 401,
            body: { message: 'Access Token이 없습니다.' },
        };
    }

    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);
        const user = await findUserByEmail(decoded.email);

        if (!user) {
            return {
                status: 404,
                body: { message: '사용자를 찾을 수 없습니다.' },
            };
        }

        return {
            status: 200,
            body: {
                email: user.email,
                name: user.name,
                login_provider: user.login_provider,
                created_at: user.created_at,
            },
        };
    } catch (err) {
        return {
            status: 401,
            body: { message: '유효하지 않은 Access Token입니다.' },
        };
    }
};
