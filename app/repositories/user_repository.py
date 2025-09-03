from typing import Dict
from ..core.connections import postgres_manager
from ..core.logger import logger
from ..core.redis_manager import redis_manager

async def get_user(username: str, password_hash: str | None = None) -> Dict | None:
    """사용자 이름으로 사용자 정보 조회"""
    query = """
        SELECT 
            id, 
            username, 
            password_hash, 
            is_active
        FROM users
        WHERE username = $1
    """
    if password_hash:
        query += " AND password_hash = $2"
        
    try:
        async with postgres_manager.get_connection() as conn:
            if password_hash:
                row = await conn.fetchrow(query, username, password_hash)

            row = await conn.fetchrow(query, username)
            return row if row else None
            
    except Exception as e:
        logger.error(f"Failed to get user by username: {e}", username=username)
        return None

async def insert_user(username: str, password_hash: str) -> Dict:
    """사용자 생성"""
    async with postgres_manager.get_connection() as conn:
        row = await conn.fetchrow(
            """
                INSERT INTO users (username, password_hash) 
                VALUES ($1, $2) 
                RETURNING id, username, created_at, updated_at, is_active
            """, 
            username,
            password_hash
        )
        
        return {
            "id": row['id'],
            "username": row['username'],
            "created_at": row['created_at'],
            "updated_at": row['updated_at'],
            "is_active": row['is_active']
        }
     

async def get_user_preferences(user_id: int) -> Dict | None:
    """사용자 선호사항 조회 (캐싱 적용)"""
    # 1. 캐시에서 먼저 조회
    cached_preferences = await redis_manager.get_cached_user_preferences(user_id)
    if cached_preferences is not None:
        return cached_preferences
    
    # 2. 캐시 미스 시 DB에서 조회
    try:
        async with postgres_manager.get_connection() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    experience_level,
                    preferred_difficulty,
                    preferred_activity_level,
                    preferred_regions,
                    preferred_group_size,
                    preferred_themes
                FROM user_preferences 
                WHERE user_id = $1
            """, user_id)
            
            if row:
                preferences = {
                    "experience_level": row['experience_level'],
                    "preferred_difficulty": row['preferred_difficulty'],
                    "preferred_activity_level": row['preferred_activity_level'],
                    "preferred_regions": row['preferred_regions'] or [],
                    "preferred_group_size": row['preferred_group_size'],
                    "preferred_themes": row['preferred_themes'] or []
                }
                
                # 3. 캐시에 저장
                await redis_manager.cache_user_preferences(user_id, preferences)
                return preferences
            
            return None
            
    except Exception as e:
        logger.error(f"Failed to get user preferences: {e}", user_id=user_id)
        return None

async def update_last_login(user_id: int, ip_address: str) -> bool:
    """사용자 마지막 로그인 정보 업데이트"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute(
                """
                    UPDATE users 
                    SET last_login_at = CURRENT_TIMESTAMP, 
                    updated_at = CURRENT_TIMESTAMP,
                    last_login_ip = $1
                    WHERE id = $2
                """, 
                ip_address,
                user_id, 
            )
            return True
            
    except Exception as e:
        logger.error(f"Failed to update last login: {e}", user_id=user_id)
        return False

async def upsert_user_preferences(user_id: int, preferences: Dict) -> bool:
    """사용자 선호사항 생성 또는 업데이트"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute("""
                INSERT INTO user_preferences (
                    user_id, 
                    experience_level, 
                    preferred_difficulty,
                    preferred_activity_level,
                    preferred_regions,
                    preferred_group_size,
                    preferred_themes
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    experience_level = EXCLUDED.experience_level,
                    preferred_difficulty = EXCLUDED.preferred_difficulty,
                    preferred_activity_level = EXCLUDED.preferred_activity_level,
                    preferred_regions = EXCLUDED.preferred_regions,
                    preferred_group_size = EXCLUDED.preferred_group_size,
                    preferred_themes = EXCLUDED.preferred_themes,
                    updated_at = CURRENT_TIMESTAMP
            """, 
                user_id,
                preferences.get('experience_level'),
                preferences.get('preferred_difficulty'),
                preferences.get('preferred_activity_level'),
                preferences.get('preferred_regions', []),
                preferences.get('preferred_group_size'),
                preferences.get('preferred_themes', [])
            )
            
            # 캐시 무효화
            await redis_manager.invalidate_user_preferences(user_id)
            
            return True
            
    except Exception as e:
        logger.error(f"Failed to upsert user preferences: {e}", user_id=user_id)
        return False

async def get_user_by_id(user_id: int) -> Dict | None:
    """사용자 ID로 사용자 정보 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            row = await conn.fetchrow(
                """
                    SELECT 
                        id, 
                        username, 
                        created_at, 
                        updated_at, 
                        last_login_at, 
                        last_login_ip
                    FROM users 
                    WHERE id = $1
                """, 
                user_id
            )
                
            if row:
                return {
                    "id": row['id'],
                    "username": row['username'],
                    "created_at": row['created_at'],
                    "updated_at": row['updated_at'],
                    "last_login_at": row['last_login_at'],
                    "last_login_ip": row['last_login_ip']
                }
            
            return None
            
    except Exception as e:
        logger.error(f"Failed to get user by ID: {e}", user_id=user_id)
        return None


