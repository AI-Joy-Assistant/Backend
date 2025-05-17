require("dotenv").config(); // .env 파일 로드

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
app.use(cors());

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
});
