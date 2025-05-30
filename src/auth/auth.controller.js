const {
    getGoogleAuthURL,
    handleGoogleCallback,
    getNewAccessTokenFromGoogle,
    handleLogout,
    fetchUserInfoFromGoogle,
    saveOrUpdateUser,
    generateAccessToken,
    generateRefreshToken,
    saveRefreshToken,
} = require('./auth.service');

const { OAuth2Client } = require('google-auth-library');

// [1] 구글 로그인 URL 생성 → 리디렉션
const googleLogin = (req, res) => {
    const url = getGoogleAuthURL();
    return res.redirect(url);
};

// [2] 구글 로그인 콜백 처리
const googleCallback = async (req, res) => {
    try {
        const code = req.query.code;
        const result = await handleGoogleCallback(code);
        // 앱의 딥링크 URL로 리다이렉트
        const redirectUrl = `frontend://auth?accessToken=${result.response.accessToken}&name=${encodeURIComponent(result.response.user.name)}`;
        return res.redirect(redirectUrl);
    } catch (error) {
        console.error('Google 콜백 처리 오류:', error);
        res.status(500).json({
            success: false,
            message: '구글 로그인 처리 중 오류가 발생했습니다.',
            error: error.message
        });
    }
};

// [3] Google ID 토큰 검증 및 로그인/회원가입 처리
const googleAuth = async (req, res) => {
    try {
        const { token } = req.body;
        
        if (!token) {
            return res.status(400).json({
                success: false,
                message: 'ID 토큰이 제공되지 않았습니다.'
            });
        }

        // Google OAuth2 클라이언트 설정
        const client = new OAuth2Client(process.env.GOOGLE_CLIENT_ID);

        // ID 토큰 검증
        const ticket = await client.verifyIdToken({
            idToken: token,
            audience: process.env.GOOGLE_CLIENT_ID
        });

        const payload = ticket.getPayload();
        
        // 사용자 정보 처리
        const userInfo = {
            email: payload.email,
            name: payload.name,
            picture: payload.picture,
            googleId: payload.sub
        };

        // 사용자 정보 저장 또는 업데이트
        const user = await saveOrUpdateUser(userInfo);

        // JWT 토큰 생성
        const accessToken = generateAccessToken(user);
        const refreshToken = generateRefreshToken(user);

        // refreshToken을 DB에 저장
        await saveRefreshToken(user.id, refreshToken);

        // JSON 응답 반환
        return res.status(200).json({
            success: true,
            data: {
                user: {
                    id: user.id,
                    email: user.email,
                    name: user.name,
                    picture: user.picture
                },
                tokens: {
                    accessToken,
                    refreshToken
                }
            }
        });

    } catch (error) {
        console.error('Google 인증 오류:', error);
        res.status(401).json({
            success: false,
            message: '인증에 실패했습니다.',
            error: error.message
        });
    }
};

// [4] accessToken 재발급
const refreshGoogleAccessToken = async (req, res) => {
    try {
        const result = await getNewAccessTokenFromGoogle(req.cookies.refreshToken);
        return res.status(result.status).json(result.body);
    } catch (error) {
        console.error('토큰 갱신 오류:', error);
        return res.status(401).json({
            success: false,
            message: '토큰 갱신에 실패했습니다.',
            error: error.message
        });
    }
};

// [5] 로그아웃 처리
const logout = async (req, res) => {
    try {
        const authHeader = req.headers.authorization;
        const token = authHeader?.split(' ')[1];
        const result = await handleLogout(token);
        return res.status(result.status).json({ message: result.message });
    } catch (error) {
        console.error('로그아웃 오류:', error);
        return res.status(500).json({
            success: false,
            message: '로그아웃 처리 중 오류가 발생했습니다.',
            error: error.message
        });
    }
};

// [6] 사용자 프로필 조회
const getGoogleProfile = async (req, res) => {
    try {
        const authHeader = req.headers.authorization;
        const token = authHeader?.split(' ')[1];
        const result = await fetchUserInfoFromGoogle(token);
        return res.status(result.status).json(result.body);
    } catch (error) {
        console.error('프로필 조회 오류:', error);
        return res.status(401).json({
            success: false,
            message: '사용자 정보 조회에 실패했습니다.',
            error: error.message
        });
    }
};

// 모든 컨트롤러 함수들을 exports
module.exports = {
    googleLogin,
    googleCallback,
    googleAuth,
    refreshGoogleAccessToken,
    logout,
    getGoogleProfile
};



