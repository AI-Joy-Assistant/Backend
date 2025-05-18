const express = require('express');
const router = express.Router();
const { googleLogin, googleCallback, refreshGoogleAccessToken,logout, getGoogleProfile } = require('./auth.controller');

router.get('/google', googleLogin);
router.get('/google/callback', googleCallback);
router.post('/token', refreshGoogleAccessToken);
router.post('/logout', logout);
router.get('/me', getGoogleProfile);

module.exports = router;
