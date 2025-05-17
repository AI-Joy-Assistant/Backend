const morgan = require("morgan");

// 개발 환경에서는 dev 로그, 운영 환경에서는 combined 로그 사용
const logger = (env) => {
    return env === "production" ? morgan("combined") : morgan("dev");
};

module.exports = logger;
