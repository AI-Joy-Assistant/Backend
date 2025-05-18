const {
    getGoogleAuthURL,
    handleGoogleCallback,
    getNewAccessTokenFromGoogle,
    handleLogout,
    fetchUserInfoFromGoogle,
} = require('./auth.service');

// [1] 구글 로그인 URL 생성 → 리디렉션
exports.googleLogin = (req, res) => {
    const url = getGoogleAuthURL();
    return res.redirect(url);
};

// [2] 구글 로그인 콜백 처리
exports.googleCallback = async (req, res) => {
    const code = req.query.code;
    const result = await handleGoogleCallback(code);
    return res
        .cookie('refreshToken', result.refreshToken, {
            httpOnly: true,
            secure: process.env.NODE_ENV === 'production',
            sameSite: 'strict',
            maxAge: 7 * 24 * 60 * 60 * 1000,
        })
        .json(result.response);
};

// [3] accessToken 재발급
exports.refreshGoogleAccessToken = async (req, res) => {
    const result = await getNewAccessTokenFromGoogle(req.cookies.refreshToken);
    return res.status(result.status).json(result.body);
};

// [4] 로그아웃 처리
exports.logout = async (req, res) => {
    const result = await handleLogout(req.cookies.userEmail);
    res.clearCookie('refreshToken');
    res.clearCookie('userEmail');
    return res.status(result.status).json({ message: result.message });
};

// [5] accessToken으로 사용자 정보 조회
exports.getGoogleProfile = async (req, res) => {
    const authHeader = req.headers.authorization;
    const token = authHeader?.split(' ')[1];
    const result = await fetchUserInfoFromGoogle(token);
    return res.status(result.status).json(result.body);
};
