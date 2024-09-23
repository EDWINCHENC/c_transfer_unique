# app/routes.py

import logging
import aiofiles
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Request, Query, Form
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from .database import get_db
from .models import Message, FileAccess
from datetime import datetime, timezone, timedelta
import os
import uuid
from typing import Optional
import mimetypes

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建FastAPI路由器
router = APIRouter()

# 设置文件上传目录
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
    logger.info(f"创建上传目录: {UPLOAD_DIR}")

# 设置文件大小限制（100MB）
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# API路由：创建新消息
@router.post("/messages/")
async def create_message(message: dict, request: Request, db: Session = Depends(get_db)):
    logger.info(f"收到来自 {request.client.host} 的新消息创建请求")
    try:
        # 使用中国时间创建消息
        china_tz = timezone(timedelta(hours=8))
        china_time = datetime.now(china_tz)
        db_message = Message(**message, created_at=china_time)
        db.add(db_message)
        db.commit()
        db.refresh(db_message)
        logger.info(f"成功创建消息: ID {db_message.id}, 创建时间: {db_message.created_at}")
        return db_message
    except Exception as e:
        logger.error(f"创建消息时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# API路由：获取所有消息
@router.get("/messages/")
async def get_messages(request: Request, db: Session = Depends(get_db), access_code: str = Query(...)):
    logger.info(f"收到来自 {request.client.host} 的获取所有消息请求")
    try:
        messages = db.query(Message).filter(Message.access_code == access_code).order_by(Message.created_at.desc()).all()
        messages_with_local_time = [
            {
                **message.__dict__,
                'created_at': message.get_created_at()
            }
            for message in messages
        ]
        logger.info(f"成功检索 {len(messages)} 条消息，访问代码: {access_code}")
        return messages_with_local_time
    except Exception as e:
        logger.error(f"检索消息时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# API路由：上传文件
@router.post("/upload/")
async def upload_file(file: UploadFile = File(...), access_code: str = Form(...), db: Session = Depends(get_db)):
    logger.info(f"收到上传文件请求: 原始文件名 = {file.filename}, 访问码 = {access_code}")
    
    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_location = os.path.join(UPLOAD_DIR, unique_filename)
    
    logger.info(f"生成的唯一文件名: {unique_filename}")
    logger.info(f"文件将被保存到: {file_location}")
    
    try:
        file_size = 0
        async with aiofiles.open(file_location, "wb") as out_file:
            while content := await file.read(8192):  # 每次读取8KB
                file_size += len(content)
                if file_size > MAX_FILE_SIZE:
                    await out_file.close()
                    os.remove(file_location)
                    logger.warning(f"文件 {file.filename} 太大，上传失败. 大小: {file_size} bytes, 限制: {MAX_FILE_SIZE} bytes")
                    raise HTTPException(status_code=413, detail="文件太大")
                await out_file.write(content)

        logger.info(f"文件 {file.filename} 上传成功，大小: {file_size} bytes")

        db_file_access = FileAccess(filename=unique_filename, access_code=access_code)
        db.add(db_file_access)
        db.commit()
        logger.info(f"文件访问记录已添加到数据库: filename={unique_filename}, access_code={access_code}")
        
        response_data = {
            "filename": unique_filename,
            "original_filename": file.filename,
            "size": file_size
        }
        logger.info(f"准备返回的响应数据: {response_data}")
        
        return JSONResponse(content=response_data, status_code=200)

    except HTTPException as he:
        logger.error(f"上传文件时发生HTTP异常: {str(he)}")
        raise he
    except Exception as e:
        logger.error(f"上传文件时发生未知错误: {str(e)}")
        if os.path.exists(file_location):
            os.remove(file_location)
            logger.info(f"删除了未完成上传的文件: {file_location}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# API路由：获取文件
@router.get("/files/{filename}")
async def get_file(
    filename: str, 
    access_code: str = Query(...), 
    db: Session = Depends(get_db)
):
    logger.info(f"收到访问文件请求: {filename}, 访问码: {access_code}")
    
    # 检查文件访问权限
    file_access = db.query(FileAccess).filter(
        FileAccess.filename == filename, 
        FileAccess.access_code == access_code
    ).first()
    
    if not file_access:
        logger.warning(f"访问被拒绝或文件未找到: {filename}")
        raise HTTPException(status_code=404, detail="文件未找到或访问被拒绝")
    
    # 构建文件路径
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        logger.error(f"文件在磁盘上未找到: {filename}")
        raise HTTPException(status_code=404, detail="文件未找到")
    
    logger.info(f"文件访问已授权: {filename}")
    
    # 获取文件大小
    file_size = os.path.getsize(file_path)
    
    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(file_path)
    
    # 设置响应头
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Length': str(file_size)
    }
    
    logger.info(f"准备发送文件: {filename}, 大小: {file_size} bytes, MIME类型: {mime_type}")
    
    # 返回文件响应
    return FileResponse(
        file_path, 
        media_type=mime_type, 
        headers=headers
    )

# API路由：删除消息、文件
@router.delete("/messages/{message_id}")
async def delete_message(message_id: int, access_code: str = Query(...), db: Session = Depends(get_db)):
    logger.info(f"收到删除消息请求: 消息ID {message_id}")
    try:
        message = db.query(Message).filter(Message.id == message_id, Message.access_code == access_code).first()
        if not message:
            logger.warning(f"消息未找到: ID {message_id}")
            raise HTTPException(status_code=404, detail="消息未找到")
        
        # 删除相关的文件或图片
        if message.type in ['image', 'file','video'] and message.content:
            file_path = os.path.join(UPLOAD_DIR, message.content)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"成功删除文件: {message.content}")
        
        # 删除数据库记录        
        db.delete(message)
        db.commit()
        logger.info(f"成功删除消息: ID {message_id}")
        return {"status": "success", "message": "消息已删除"}
    except Exception as e:
        logger.error(f"删除消息时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")
