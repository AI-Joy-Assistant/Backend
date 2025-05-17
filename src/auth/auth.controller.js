const { getGoogleAuthURL, getGoogleUserInfo, getNewAccessTokenFromGoogle, fetchUserInfoFromGoogle } = require('./auth.service');
const { updateUserStatus } = require('./auth.repository')
exports.googleLogin = (req, res) => {
    const url = getGoogleAuthURL();
    return res.redirect(url);
};

exports.googleCallback = async (req, res) => {
    const code = req.query.code;
    try {
        const { accessToken, refreshToken,expiresIn, user } = await getGoogleUserInfo(code);

        res.cookie('refreshToken', refreshToken, {
            httpOnly: true,
            secure: process.env.NODE_ENV === 'production',
            sameSite: 'strict',
            maxAge: 7 * 24 * 60 * 60 * 1000,
        });

        return res.json({
            message: `환영합니다, ${user.name}님!`,
            accessToken,
            expiresIn,
            user,
        });
    } catch (err) {
        console.error('구글 로그인 실패:', err.message);
        return res.status(500).send('로그인 실패');
    }
};

//accessToken 만료 시 → 구글에게 재발급 요청
exports.refreshGoogleAccessToken = async (req, res) => {
    const refreshToken = req.cookies.refreshToken;

    if (!refreshToken) {
        return res.status(401).json({ message: 'Refresh Token이 없습니다.' });
    }

    try {
        const { accessToken, expiresIn } = await getNewAccessTokenFromGoogle(refreshToken);
        return res.json({ accessToken, expiresIn });
    } catch (err) {
        return res.status(500).json({ message: err.message || 'accessToken 재발급 실패' });
    }
};

exports.logout = async (req, res) => {
    const email = req.cookies.userEmail;

    if (email) {
        await updateUserStatus(email, 'OFFLINE');
    }
    res.clearCookie('refreshToken');
    res.clearCookie('userEmail');
    return res.status(200).json({message: '로그아웃 완료'});
};

exports.getGoogleProfile = async (req, res) => {
    const authHeader = req.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return res.status(401).json({ message: 'Access Token이 없습니다.' });
    }

    const accessToken = authHeader.split(' ')[1];

    try {
        const user = await fetchUserInfoFromGoogle(accessToken);
        return res.json(user);
    } catch (err) {
        return res.status(401).json({ message: '유효하지 않은 accessToken입니다.' });
    }
};
