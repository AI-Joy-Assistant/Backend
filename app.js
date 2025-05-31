require("dotenv").config(); // .env 파일 로드
const cookieParser = require('cookie-parser');

const express = require("express");
const compression = require("compression");
const methodOverride = require("method-override");
const cors = require("cors");
const swaggerUI = require("swagger-ui-express");
const swaggerDocument = require("./config/swagger.json");

const app = express();

// 기본 설정
app.use(compression());
app.disable("x-powered-by");
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(methodOverride());
app.use(cookieParser());
app.use(cors({
    origin: [
        'http://localhost:8081', // Expo 웹(현재 실행 중인 프론트엔드 주소)
        'http://localhost:19006', // Expo 웹 기본 포트
        'http://localhost:5173'   // 기존에 사용하던 포트(필요시)
    ],
    credentials: true               // ✅ 쿠키 허용
}));


// Swagger 문서
app.use("/api-docs", swaggerUI.serve, swaggerUI.setup(swaggerDocument, { explorer: true }));

// 라우터 등록
const authRouter = require("./src/auth/auth.router");
app.use('/auth', authRouter);

// 에러 핸들러 (utils/errorHandler.js가 있을 경우)
// const { errorHandler } = require("./utils/errorHandler");
// app.use(errorHandler);

// 서버 시작
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`🚀 Server running on http://localhost:${PORT}`);
    console.log(`Swagger URL: http://localhost:${PORT}/api-docs`);
    console.log(`http://localhost:3000/auth/google`)
});
