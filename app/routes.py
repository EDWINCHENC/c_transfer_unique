# app/routes.py

import aiofiles
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Request, Query, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi import Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import get_db
from .models import Message, FileAccess
from datetime import datetime, timezone, timedelta
import os
import uuid
from typing import Optional
import mimetypes
from loguru import logger
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

# 创建FastAPI路由器
router = APIRouter()

# 设置速率限制器（可以根据需求调整速率限制）
limiter = Limiter(key_func=get_remote_address)

# 设置文件上传目录
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
    logger.info(f"创建上传目录: {UPLOAD_DIR}")

# 设置文件大小限制（100MB）
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# 定义每个 IP 最大允许创建的不同 access_code 数量
MAX_ACCESS_CODES_PER_IP = 5

@router.post("/messages/")
@limiter.limit("10/minute")  # 每分钟最多10次请求
async def create_message(message: dict, request: Request, db: Session = Depends(get_db)):
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    logger.info(f"收到来自 {ip} 的新消息创建请求")

    try:
        # 检查该 IP 创建的不同 access_code 数量
        distinct_access_codes = db.query(func.count(func.distinct(Message.access_code))).filter(
            Message.creator_ip == ip
        ).scalar()

        if distinct_access_codes >= MAX_ACCESS_CODES_PER_IP:
            logger.warning(f"IP {ip} 已达到最大允许创建的不同 access_code 数量限制 ({MAX_ACCESS_CODES_PER_IP})")
            raise HTTPException(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                detail=f"您已达到可创建的最大 access_code 数量限制 ({MAX_ACCESS_CODES_PER_IP})"
            )

        # 检查是否是新的 access_code
        existing_access_code = db.query(Message).filter(
            Message.access_code == message['access_code'],
            Message.creator_ip == ip
        ).first()

        if existing_access_code:
            # 如果 access_code 已存在，直接创建新消息
            china_tz = timezone(timedelta(hours=8))
            china_time = datetime.now(china_tz)
            db_message = Message(**message, created_at=china_time, creator_ip=ip)
            db.add(db_message)
            db.commit()
            db.refresh(db_message)
            logger.info(f"成功创建消息: ID {db_message.id}, 创建时间: {db_message.created_at}, IP: {ip}, Access Code: {message['access_code']} (已存在)")
        else:
            # 如果是新的 access_code，检查是否超过限制
            if distinct_access_codes < MAX_ACCESS_CODES_PER_IP:
                china_tz = timezone(timedelta(hours=8))
                china_time = datetime.now(china_tz)
                db_message = Message(**message, created_at=china_time, creator_ip=ip)
                db.add(db_message)
                db.commit()
                db.refresh(db_message)
                logger.info(f"成功创建消息: ID {db_message.id}, 创建时间: {db_message.created_at}, IP: {ip}, Access Code: {message['access_code']} (新)")
            else:
                logger.warning(f"IP {ip} 尝试创建新的 access_code，但已达到限制")
                raise HTTPException(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"您已达到可创建的最大 access_code 数量限制 ({MAX_ACCESS_CODES_PER_IP})"
                )

        return db_message

    except HTTPException as he:
        logger.error(f"创建消息时发生HTTP异常: {he.detail}")
        raise he
    except Exception as e:
        logger.error(f"创建消息时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# API路由：获取所有消息
@router.get("/messages/")
@limiter.limit("20/minute")  # 例如，每分钟最多20次请求
async def get_messages(request: Request, db: Session = Depends(get_db), access_code: str = Query(...)):
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    logger.info(f"收到来自 {ip} 的获取所有消息请求，access_code: {access_code}")
    try:
        messages = db.query(Message).filter(Message.access_code == access_code).order_by(Message.created_at.desc()).all()
        messages_with_local_time = [
            {
                **message.__dict__,
                'created_at': message.get_created_at()
            }
            for message in messages
        ]
        logger.info(f"成功检索 {len(messages)} 条消息，访问代码: {access_code}, IP: {ip}")
        return messages_with_local_time
    except Exception as e:
        logger.error(f"检索消息时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# API路由：上传文件
@router.post("/upload/")
@limiter.limit("10/minute")
async def upload_file(file: UploadFile = File(...), access_code: str = Form(...), db: Session = Depends(get_db), request: Request = None):
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    logger.info(f"收到来自 {ip} 的上传文件请求: 原始文件名 = {file.filename}, 访问码 = {access_code}")

    # 创建以访问码命名的子文件夹
    subfolder = os.path.join(UPLOAD_DIR, access_code)
    if not os.path.exists(subfolder):
        os.makedirs(subfolder)
        logger.info(f"创建子文件夹: {subfolder}")

    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_location = os.path.join(subfolder, unique_filename)

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
                    logger.warning(f"文件 {file.filename} 太大，上传失败. 大小: {file_size} bytes, 限制: {MAX_FILE_SIZE} bytes, IP: {ip}")
                    raise HTTPException(status_code=413, detail="文件太大")
                await out_file.write(content)

        logger.info(f"文件 {file.filename} 上传成功，大小: {file_size} bytes, IP: {ip}")

        # 在数据库中只存储唯一文件名
        db_file_access = FileAccess(filename=unique_filename, access_code=access_code)
        db.add(db_file_access)
        db.commit()
        logger.info(f"文件访问记录已添加到数据库: filename={unique_filename}, access_code={access_code}, IP: {ip}")
        
        response_data = {
            "filename": unique_filename,
            "original_filename": file.filename,
            "size": file_size
        }
        logger.info(f"准备返回的响应数据: {response_data}, IP: {ip}")
        
        return JSONResponse(content=response_data, status_code=200)

    except HTTPException as he:
        logger.error(f"上传文件时发生HTTP异常: {he.detail}, IP: {ip}")
        raise he
    except Exception as e:
        logger.error(f"上传文件时发生未知错误: {str(e)}, IP: {ip}")
        if os.path.exists(file_location):
            os.remove(file_location)
            logger.info(f"删除了未完成上传的文件: {file_location}, IP: {ip}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# 为video获取视频文件
@router.get("/stream/{filename}")
@limiter.limit("60/minute")
async def stream_video(
    filename: str,
    access_code: str = Query(...),
    db: Session = Depends(get_db),
):    
    # 检查文件访问权限
    file_access = db.query(FileAccess).filter(
        FileAccess.filename == filename, 
        FileAccess.access_code == access_code
    ).first()
    
    if not file_access:
        raise HTTPException(status_code=404, detail="文件未找到或访问被拒绝")
    
    file_path = os.path.join(UPLOAD_DIR, access_code, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件未找到")
    
    file_size = os.path.getsize(file_path)
    logger.info(f"准备发送文件<video>元素: {filename}, 大小: {file_size} bytes")
    
    return FileResponse(file_path)


# API路由：获取文件
@router.get("/files/{filename}")
@limiter.limit("30/minute")
async def get_file(
    filename: str, 
    access_code: str = Query(...), 
    db: Session = Depends(get_db),
    request: Request = None
):
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    logger.info(f"收到来自 {ip} 的访问文件请求: {filename}, 访问码: {access_code}")
    
    # 检查文件访问权限
    file_access = db.query(FileAccess).filter(
        FileAccess.filename == filename, 
        FileAccess.access_code == access_code
    ).first()
    
    if not file_access:
        logger.warning(f"访问被拒绝或文件未找到: {filename}, IP: {ip}")
        raise HTTPException(status_code=404, detail="文件未找到或访问被拒绝")
    
    # 构建文件路径
    file_path = os.path.join(UPLOAD_DIR, access_code, filename)
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        logger.error(f"文件在磁盘上未找到: {filename}, IP: {ip}")
        raise HTTPException(status_code=404, detail="文件未找到")
    
    logger.info(f"文件访问已授权: {filename}, IP: {ip}")
    
    # 获取文件大小
    file_size = os.path.getsize(file_path)
    
    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(file_path)
    
    # 设置响应头
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Length': str(file_size)
    }
    
    logger.info(f"准备发送文件: {filename}, 大小: {file_size} bytes, MIME类型: {mime_type}, IP: {ip}")
    
    # 返回文件响应
    return FileResponse(
        file_path, 
        media_type=mime_type, 
        headers=headers
    )

# API路由：删除文件
@router.delete("/messages/{message_id}")
@limiter.limit("5/minute")
async def delete_message(message_id: int, access_code: str = Query(...), db: Session = Depends(get_db), request: Request = None):
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.client.host
    logger.info(f"收到来自 {ip} 的删除消息请求: 消息ID {message_id}")
    try:
        message = db.query(Message).filter(Message.id == message_id, Message.access_code == access_code).first()
        if not message:
            logger.warning(f"消息未找到: ID {message_id}, IP: {ip}")
            raise HTTPException(status_code=404, detail="消息未找到")
        
        # 删除相关的文件或图片
        if message.type in ['image', 'file', 'video'] and message.content:
            file_path = os.path.join(UPLOAD_DIR, access_code, message.content)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"成功删除文件: {message.content}, IP: {ip}")
                
                # 如果文件夹为空，删除文件夹
                folder_path = os.path.dirname(file_path)
                if not os.listdir(folder_path):
                    os.rmdir(folder_path)
                    logger.info(f"删除空文件夹: {folder_path}, IP: {ip}")
        
        # 删除数据库记录        
        db.delete(message)
        db.commit()
        logger.info(f"成功删除消息: ID {message_id}, IP: {ip}")
        return {"status": "success", "message": "消息已删除"}
    except HTTPException as he:
        logger.error(f"删除消息时发生HTTP异常: {he.detail}, IP: {ip}")
        raise he
    except Exception as e:
        logger.error(f"删除消息时发生错误: {str(e)}, IP: {ip}")
        raise HTTPException(status_code=500, detail="服务器内部错误")
