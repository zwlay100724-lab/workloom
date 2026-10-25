import os

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException
from redis.exceptions import RedisError
from shared.db.database import get_session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

#代码实现了一个 FastAPI 健康检查端点，用于检测 Redis 服务的连通性。
router = APIRouter(prefix="/health",tags=["health"],)
REDIS_URL = os.getenv("REDIS_URL","redis://localhost:6379/0",)


@router.get("")
def health():
    """轻量存活检查，供 compose healthcheck / smoke 脚本使用。"""
    return {"status": "ok"}


@router.get("/ready")
def readiness(session: Session = Depends(get_session)):
    """数据库可用才返回 200；Redis 是否可用由 /health/redis 单独报告。"""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="database unavailable",
        ) from exc
    return {"status": "ready"}


@router.get("/redis")
async def redis_health():
    try:
        async with redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
        ) as client:
            pong = await client.ping()
    except RedisError:
        # Redis 不可用时返回 false,而不是抛 500
        return {"redis": False}
    return {"redis": pong}