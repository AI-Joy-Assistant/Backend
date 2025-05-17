require("dotenv").config();

const appConfig = {
    env: process.env.NODE_ENV || "development",
    port: process.env.PORT || 3000,
    apiPrefix: "/api/v1",
    jwt: {
        secret: process.env.JWT_SECRET || "default-secret",
        expiresIn: "7d",
    },
};

module.exports = appConfig;