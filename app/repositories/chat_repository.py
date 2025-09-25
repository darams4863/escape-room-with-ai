"""채팅 관련 Repository"""

import json
from typing import Dict

from ..core.connections import postgres_manager
from ..core.logger import logger


# chat_service.py에서 사용
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

async def get_latest_session_by_user_id(user_id: int) -> Dict | None:
    """사용자의 최신 세션 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            row = await conn.fetchrow(
                """
                    SELECT 
                        id,
                        session_id,
                        user_id,
                        conversation_history,
                        created_at,
                        updated_at
                    FROM chat_sessions 
                    WHERE user_id = $1
                    ORDER BY updated_at DESC
                    LIMIT 1
                """, 
                str(user_id)
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Failed to get latest session: {e}", user_id=user_id)
        return None

# async def get_session_by_id(session_id: str) -> Dict | None:
#     """세션 ID로 세션 조회"""
#     try:
#         async with postgres_manager.get_connection() as conn:
#             row = await conn.fetchrow(
#                 """
#                     SELECT 
#                         id,
#                         session_id,
#                         user_id,
#                         conversation_history,
#                         created_at,
#                         updated_at
#                     FROM chat_sessions 
#                     WHERE session_id = $1
#                 """, 
#                 session_id
#             )
#             return dict(row) if row else None
#     except Exception as e:
#         logger.error(f"Failed to get session: {e}", session_id=session_id)
#         return None

async def update_session(session_id: str, conversation_history: str) -> bool:
    """세션 대화 기록 업데이트"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE chat_sessions 
                SET conversation_history = $1, 
                updated_at = CURRENT_TIMESTAMP
                WHERE session_id = $2
                """, 
                conversation_history, 
                session_id
            )
            return True
    except Exception as e:
        logger.error(f"Failed to update session: {e}")
        return False