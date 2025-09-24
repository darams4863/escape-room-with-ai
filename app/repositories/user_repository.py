from typing import Dict

from ..core.connections import postgres_manager
from ..core.constants import EXPERIENCE_LEVELS
from ..core.logger import logger
from ..models.user import User


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


async def get_user_by_id(user_id: int) -> User | None:
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
                        is_active
                    FROM users 
                    WHERE id = $1
                """, 
                user_id
            )
                
            if row:
                return User(
                    id=row['id'],
                    username=row['username'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    is_active=row['is_active']
                )
            
            return None
            
    except Exception as e:
        logger.error(f"Failed to get user by ID: {e}", user_id=user_id)
        return None


async def get_user_preferences(user_id: int) -> Dict | None:
    """사용자 선호사항 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            row = await conn.fetchrow(
                """
                    SELECT 
                        experience_level,
                        preferred_difficulty,
                        preferred_activity_level,
                        preferred_regions,
                        preferred_group_size,
                        preferred_themes,
                        experience_count
                    FROM user_preferences 
                    WHERE user_id = $1
                """, 
                user_id
            )
            
            if row:
                user_prefs = {
                    "experience_level": row['experience_level'],
                    "preferred_difficulty": row['preferred_difficulty'],
                    "preferred_activity_level": row['preferred_activity_level'],
                    "preferred_regions": row['preferred_regions'] or [],
                    "preferred_group_size": row['preferred_group_size'],
                    "preferred_themes": row['preferred_themes'] or [],
                    "experience_count": row['experience_count'] or 0
                }
                
                # 경험 횟수 기반으로 선호도 정규화 (EXPERIENCE_LEVELS 활용)
                def get_experience_level(count: int) -> str:
                    """경험 횟수로 등급 반환"""
                    for level, info in EXPERIENCE_LEVELS.items():
                        if info["min_count"] <= count <= info["max_count"]:
                            return level
                    return list(EXPERIENCE_LEVELS.keys())[0]  # 기본값
                
                if 'experience_count' in user_prefs and user_prefs['experience_count'] is not None:
                    count = user_prefs['experience_count']
                    
                    # experience_level 자동 설정
                    user_prefs['experience_level'] = get_experience_level(count)
                    
                    # preferred_difficulty가 없거나 0이면 추천 난이도로 설정
                    if not user_prefs.get('preferred_difficulty') or user_prefs.get('preferred_difficulty') == 0:
                        level_info = EXPERIENCE_LEVELS.get(user_prefs['experience_level'], {})
                        difficulties = level_info.get('recommended_difficulty', [2])
                        user_prefs['preferred_difficulty'] = sum(difficulties) // len(difficulties)  # 평균값
                    
                    # EXPERIENCE_LEVELS에서 추천 테마도 설정 (기본값이 없을 때)
                    if not user_prefs.get('preferred_themes'):
                        level_info = EXPERIENCE_LEVELS.get(user_prefs['experience_level'], {})
                        user_prefs['preferred_themes'] = level_info.get('recommended_themes', [])
                
                return user_prefs
            
            return None
            
    except Exception as e:
        logger.error(f"Failed to get user preferences: {e}", user_id=user_id)
        return None


async def upsert_user_preferences(user_id: int, preferences: Dict) -> bool:
    """사용자 선호사항 생성 또는 업데이트"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute(
                """
                    INSERT INTO user_preferences (
                        user_id, 
                        experience_level, 
                        preferred_difficulty,
                        preferred_activity_level,
                        preferred_regions,
                        preferred_group_size,
                        preferred_themes,
                        experience_count
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET 
                        experience_level = EXCLUDED.experience_level,
                        preferred_difficulty = EXCLUDED.preferred_difficulty,
                        preferred_activity_level = EXCLUDED.preferred_activity_level,
                        preferred_regions = EXCLUDED.preferred_regions,
                        preferred_group_size = EXCLUDED.preferred_group_size,
                        preferred_themes = EXCLUDED.preferred_themes,
                        experience_count = EXCLUDED.experience_count,
                        updated_at = CURRENT_TIMESTAMP
                """, 
                user_id,
                preferences.get('experience_level'),
                preferences.get('preferred_difficulty'),
                preferences.get('preferred_activity_level'),
                preferences.get('preferred_regions', []),
                preferences.get('preferred_group_size'),
                preferences.get('preferred_themes', []),
                preferences.get('experience_count', 0)
            )
            
            return True
            
    except Exception as e:
        logger.error(f"Failed to upsert user preferences: {e}", user_id=user_id)
        return False


