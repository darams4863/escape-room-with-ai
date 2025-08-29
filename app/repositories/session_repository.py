"""세션 관련 Repository - 함수 기반 구현"""

import json
from datetime import datetime, timedelta
from typing import List, Dict
from ..core.connections import postgres_manager, redis_manager
from ..core.logger import logger

# 세션 관련 함수들
async def get_user_session(user_id: str) -> Dict | None:
    """사용자의 최신 세션 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            row = await conn.fetchrow("""
                SELECT session_id, conversation_history, updated_at
                FROM chat_sessions 
                WHERE user_id = $1
                ORDER BY updated_at DESC
                LIMIT 1
            """, user_id)
            
            if row:
                return {
                    "session_id": row['session_id'],
                    "conversation_history": row['conversation_history'],
                    "updated_at": row['updated_at']
                }
            return None
            
    except Exception as e:
        logger.error(f"Failed to get user session: {e}", user_id=user_id)
        return None

async def create_session(user_id: str, session_id: str) -> bool:
    """새 세션 생성"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute(
                """
                    INSERT INTO chat_sessions (session_id, user_id, conversation_history, created_at, updated_at)
                    VALUES ($1, $2, $3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, 
                session_id, 
                user_id,
                json.dumps({"messages": []}, ensure_ascii=False)
            )
            return True
            
    except Exception as e:
        logger.error(f"Failed to create session: {e}", user_id=user_id, session_id=session_id)
        return False

async def update_session(session_id: str, conversation_history: str) -> bool:
    """세션 대화 기록 업데이트"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute("""
                UPDATE chat_sessions 
                SET conversation_history = $1, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = $2
            """, conversation_history, session_id)
            return True
            
    except Exception as e:
        logger.error(f"Failed to update session: {e}", session_id=session_id)
        return False

async def get_session_by_id(session_id: str) -> Dict | None:
    """세션 ID로 세션 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            row = await conn.fetchrow("""
                SELECT session_id, user_id, conversation_history, updated_at
                FROM chat_sessions 
                WHERE session_id = $1
            """, session_id)
            
            if row:
                return {
                    "session_id": row['session_id'],
                    "user_id": row['user_id'],
                    "conversation_history": row['conversation_history'],
                    "updated_at": row['updated_at']
                }
            return None
            
    except Exception as e:
        logger.error(f"Failed to get session by ID: {e}", session_id=session_id)
        return None


async def delete_expired_sessions(hours: int = 24) -> int:
    """만료된 세션 삭제"""
    try:
        async with postgres_manager.get_connection() as conn:
            result = await conn.execute("""
                DELETE FROM chat_sessions 
                WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '1 hour' * $1
            """, hours)
            return result.split()[-1] if result else 0
            
    except Exception as e:
        logger.error(f"Failed to delete expired sessions: {e}")
        return 0

# Redis 관련 함수들
async def cache_session_to_redis(user_id: str, session_id: str) -> bool:
    """세션을 Redis에 캐시"""
    try:
        session_meta = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "last_activity": datetime.utcnow().isoformat(),
            "status": "active"
        }
        
        # 사용자별 세션 매핑 저장
        await redis_manager.set(
            key=f"user_session:{user_id}",
            value=json.dumps(session_meta, ensure_ascii=False),
            ex=86400  # 24시간
        )
        
        # 세션 메타데이터 저장
        await redis_manager.set(
            key=f"session_meta:{session_id}",
            value=json.dumps(session_meta, ensure_ascii=False),
            ex=86400
        )
        
        # 사용자 세션 목록에 추가
        await redis_manager.sadd(f"user_sessions:{user_id}", session_id)
        await redis_manager.expire(f"user_sessions:{user_id}", 86400)
        
        logger.debug(f"Session cached to Redis", user_id=user_id, session_id=session_id)
        return True
        
    except Exception as e:
        logger.error(f"Failed to cache session to Redis: {e}", user_id=user_id, session_id=session_id)
        return False

async def get_session_from_redis(session_id: str) -> Dict | None:
    """Redis에서 세션 조회"""
    try:
        session_meta = await redis_manager.get(f"session_meta:{session_id}")
        if session_meta:
            return json.loads(session_meta)
        return None
        
    except Exception as e:
        logger.error(f"Failed to get session from Redis: {e}", session_id=session_id)
        return None

async def update_session_activity(session_id: str) -> bool:
    """세션 활동 시간 업데이트"""
    try:
        session_meta = await redis_manager.get(f"session_meta:{session_id}")
        if session_meta:
            meta_data = json.loads(session_meta)
            meta_data["last_activity"] = datetime.utcnow().isoformat()
            
            await redis_manager.set(
                key=f"session_meta:{session_id}",
                value=json.dumps(meta_data, ensure_ascii=False),
                ex=86400
            )
            return True
        return False
        
    except Exception as e:
        logger.error(f"Failed to update session activity: {e}", session_id=session_id)
        return False

async def validate_user_session(user_id: int, session_id: str) -> bool:
    """사용자 세션 유효성 검증"""
    try:
        # 1. Redis에서 세션 메타데이터 확인
        session_meta = await redis_manager.get(f"session_meta:{session_id}")
        if not session_meta:
            return False
        
        meta_data = json.loads(session_meta)
        if meta_data.get("user_id") != user_id:
            logger.warning(f"Session user mismatch", session_user_id=meta_data.get("user_id"), request_user_id=user_id)
            return False
        
        # 2. 세션이 활성 상태인지 확인
        if meta_data.get("status") != "active":
            return False
        
        # 3. 세션 만료 시간 확인
        last_activity = datetime.fromisoformat(meta_data.get("last_activity", "1970-01-01T00:00:00"))
        if datetime.utcnow() - last_activity > timedelta(hours=24):
            logger.info(f"Session expired", session_id=session_id)
            return False
        
        # 4. 사용자 세션 목록에 존재하는지 확인
        user_sessions = await redis_manager.smembers(f"user_sessions:{user_id}")
        if session_id not in user_sessions:
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Session validation error: {e}", session_id=session_id)
        return False
