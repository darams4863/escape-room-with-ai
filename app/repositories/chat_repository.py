import json
from datetime import datetime, timedelta
from typing import List, Dict
from ..core.connections import postgres_manager, redis_manager
from ..core.logger import logger


async def update_session_activity(session_id: str) -> bool:
    """세션 활동 시간 업데이트"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute("""
                UPDATE chat_sessions 
                SET updated_at = CURRENT_TIMESTAMP
                WHERE session_id = $1
            """, session_id)
            return True 
    except Exception as e:
        logger.error(f"Failed to update session activity: {e}", session_id=session_id)
        return False
