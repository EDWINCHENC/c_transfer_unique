# app/main.py
import sys, os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from loguru import logger
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from .routes import router
from .database import engine
from .models import Base

# 确保日志目录存在
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 配置 Loguru
logger.remove()  # 移除默认的日志记录器
logger.add(sys.stderr, format="{time} {level} {message}", level="INFO")  # 添加终端输出
logger.add(os.path.join(LOG_DIR, "file_transfer.log"), rotation="1 day", retention="7 days", format="{time} {level} {message}")
# 初始化数据库
Base.metadata.create_all(bind=engine)

# 初始化Limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI()

# 添加速率限制中间件
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)

# 包含路由
app.include_router(router)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://t.cdef.cc", "https://t.lfei.cc"],  # 允许所有源，您可以替换为特定的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

# 添加慢速API中间件以记录所有请求的源IP
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # 尝试获取真实的客户端 IP 地址
    real_ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    method = request.method
    url = request.url.path
    logger.info(f"Incoming request from {real_ip}: {method} {url}")
    response = await call_next(request)
    return response

@app.exception_handler(429)
async def rate_limit_handler(request: Request, exc):
    real_ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    logger.warning(f"Rate limit exceeded for {real_ip}: {request.method} {request.url.path}")
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
