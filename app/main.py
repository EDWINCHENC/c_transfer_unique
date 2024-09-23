from fastapi import FastAPI
from .routes import router
from .database import engine
from .models import Base

app = FastAPI()

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 包含路由
app.include_router(router)

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
