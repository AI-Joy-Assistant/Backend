const express = require('express');
const router = express.Router();
const { googleLogin, googleCallback, refreshGoogleAccessToken, logout, getGoogleProfile, googleAuth } = require('./auth.controller');

router.post('/google', googleAuth);
router.get('/google/callback', googleCallback);
router.post('/token/refresh', refreshGoogleAccessToken);
router.post('/logout', logout);
router.get('/profile', getGoogleProfile);

module.exports = router;
