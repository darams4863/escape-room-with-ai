from typing import Dict, Any
from ..core.connections import connections
from ..core.logger import logger
from ..core.exceptions import CustomError
from ..models.user import User, UserCreate, UserLogin, Token
from ..utils.auth import password_manager, jwt_manager


class UserService:
    """사용자 관련 비즈니스 로직"""
    
    async def create_user(self, user_data: UserCreate) -> User:
        """사용자 생성"""
        try:
            # 비밀번호 해싱
            hashed_password = password_manager.hash_password(user_data.password)
            
            # 데이터베이스에 사용자 저장
            async with connections.postgres.get_connection() as conn:
                # 중복 체크
                existing_user = await conn.fetchrow(
                    """
                    SELECT 
                        id 
                    FROM users 
                    WHERE username = $1
                    """, 
                    user_data.username
                )
                
                if existing_user:
                    raise CustomError("USER_ALREADY_EXISTS", username=user_data.username)
                
                # 새 사용자 생성
                user_record = await conn.fetchrow(
                    """
                    INSERT INTO users (username, password_hash) 
                    VALUES ($1, $2) 
                    RETURNING id, username, created_at, updated_at, is_active
                    """,
                    user_data.username, hashed_password
                )
                
                logger.info(f"New user created: {user_data.username}")
                
                return User(
                    id=user_record['id'],
                    username=user_record['username'],
                    created_at=user_record['created_at'],
                    updated_at=user_record['updated_at'],
                    is_active=user_record['is_active']
                )
                
        except CustomError:
            raise
        except Exception as e:
            logger.error(
                f"User creation error: {e}", 
                error_type="database_error",
                username=user_data.username
            )
            raise CustomError("DB_ERROR", "사용자 생성 중 데이터베이스 오류가 발생했습니다.")
    
    async def authenticate_user(self, login_data: UserLogin, client_ip: str = None) -> Token:
        """사용자 인증 및 토큰 발급"""
        try:
            async with connections.postgres.get_connection() as conn:
                # 사용자 조회
                user_record = await conn.fetchrow(
                    """
                    SELECT 
                        id, 
                        username, 
                        password_hash, 
                        is_active 
                    FROM users 
                    WHERE username = $1
                    """,
                    login_data.username
                )
                
                if not user_record:
                    raise CustomError("USER_NOT_FOUND", username=login_data.username)
                
                if not user_record['is_active']:
                    raise CustomError("INACTIVE_USER", username=login_data.username)
                
                # 비밀번호 검증
                if not password_manager.verify_password(
                    login_data.password, 
                    user_record['password_hash']
                ):
                    raise CustomError("INVALID_CREDENTIALS")
                
                # JWT 토큰 생성
                token_data = jwt_manager.create_access_token(
                    user_id=user_record['id'],
                    username=user_record['username']
                )
                
                # Redis에 토큰 저장 (1시간 만료)
                await self._store_token_in_redis(
                    user_id=user_record['id'],
                    token=token_data['access_token'],
                    expire_seconds=3600  # 1시간
                )
                
                # 🆕 로그인 IP 및 시간 업데이트
                if client_ip:
                    await conn.execute(
                        """
                        UPDATE users 
                        SET last_login_ip = $1, last_login_at = CURRENT_TIMESTAMP 
                        WHERE id = $2
                        """,
                        client_ip, user_record['id']
                    )
                    logger.info(f"Updated login info for user {user_record['username']}", client_ip=client_ip)
                
                logger.info(
                    f"User authenticated: {login_data.username}",
                    client_ip=client_ip,
                    user_id=user_record['id']
                )
                
                return Token(**token_data)
                
        except CustomError:
            raise
        except Exception as e:
            logger.error(
                f"Authentication error: {e}",
                error_type="database_error",
                username=login_data.username
            )
            raise CustomError("DB_ERROR", "인증 중 데이터베이스 오류가 발생했습니다.")
    
    async def get_user_by_id(self, user_id: int) -> User | None:
        """사용자 ID로 사용자 조회"""
        try:
            async with connections.postgres.get_connection() as conn:
                user_record = await conn.fetchrow(
                    """
                    SELECT 
                        id, 
                        username, 
                        created_at, 
                        updated_at, 
                        is_active 
                    FROM users 
                    WHERE id = $1 AND is_active = TRUE
                    """,
                    user_id
                )
                
                if not user_record:
                    return None
                
                return User(
                    id=user_record['id'],
                    username=user_record['username'],
                    created_at=user_record['created_at'],
                    updated_at=user_record['updated_at'],
                    is_active=user_record['is_active']
                )
                
        except Exception as e:
            logger.error(f"User lookup error: {e}")
            return None
    
    async def verify_token_and_get_user(self, token: str) -> User | None:
        """토큰 검증 및 사용자 반환 (Redis 확인 포함)"""
        try:
            # JWT 토큰 검증
            payload = jwt_manager.verify_token(token)
            if not payload:
                return None
            
            user_id = payload.get('user_id')
            if not user_id:
                return None
            
            # Redis에서 토큰 확인
            is_valid = await self._verify_token_in_redis(user_id, token)
            if not is_valid:
                logger.warning(f"Token not found in Redis", user_id=user_id)
                return None
            
            # 사용자 조회
            user = await self.get_user_by_id(user_id)
            return user
            
        except Exception as e:
            logger.error(f"Token verification error: {e}")
            return None
    
    async def logout_user(self, user_id: int, token: str) -> bool:
        """사용자 로그아웃 (Redis에서 토큰 삭제)"""
        try:
            await self._remove_token_from_redis(user_id, token)
            logger.info(f"User logged out", user_id=user_id)
            return True
        except Exception as e:
            logger.error(f"Logout error: {e}", user_id=user_id)
            return False
    
    # Redis 토큰 관리 헬퍼 메서드
    async def _store_token_in_redis(self, user_id: int, token: str, expire_seconds: int = 3600):
        """Redis에 토큰 저장"""
        try:
            await connections.redis.set(
                key=f"user_token:{user_id}:{token[-8:]}",  # 토큰 마지막 8자리로 키 생성
                value=token,
                ex=expire_seconds
            )
        except Exception as e:
            logger.error(f"Failed to store token in Redis: {e}", user_id=user_id)
            raise
    
    async def _verify_token_in_redis(self, user_id: int, token: str) -> bool:
        """Redis에서 토큰 확인"""
        try:
            stored_token = await connections.redis.get(f"user_token:{user_id}:{token[-8:]}")
            return stored_token == token
        except Exception as e:
            logger.error(f"Failed to verify token in Redis: {e}", user_id=user_id)
            return False
    
    async def _remove_token_from_redis(self, user_id: int, token: str):
        """Redis에서 토큰 삭제"""
        try:
            await connections.redis.delete(f"user_token:{user_id}:{token[-8:]}")
        except Exception as e:
            logger.error(f"Failed to remove token from Redis: {e}", user_id=user_id)
            raise


user_service = UserService()
