"""사용자 관련 비즈니스 로직 (함수 기반)"""

from ..core.connections import connections
from ..core.logger import logger
from ..core.exceptions import CustomError
from ..core.monitor import track_performance, track_error, track_user_registration, track_user_login
from ..models.user import User, Token
from ..utils.auth import password_manager, jwt_manager

# Repository 함수들 import
from ..repositories.user_repository import (
    get_user,
    insert_user,
    # get_user_preferences,
    # upsert_user_preferences,
    get_user_by_id,
    update_last_login, 
)


@track_performance("user_creation")
async def create_user(username: str, password: str) -> User:
    """사용자 생성"""
    try:
        # 중복 체크
        existing_user = await get_user(
            username, 
            # password_manager.hash_password(password)
        )

        if existing_user:
            raise CustomError(
                "USER_ALREADY_EXISTS", 
                username=username
            )

        # 비밀번호 해싱
        hashed_password = password_manager.hash_password(password)
        
        # 새 사용자 생성
        user_record = await insert_user(username, hashed_password)
        
        # 사용자 등록 메트릭 추적
        track_user_registration()
        
        logger.info(f"New user created: {user_record}")
            
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
        track_error("database_error", "/auth/register", "POST", None)
        logger.error(
            f"User creation error: {e}", 
            error_type="database_error",
            username=username
        )
        raise CustomError("DB_ERROR", "사용자 생성 중 데이터베이스 오류가 발생했습니다.")

@track_performance("user_authentication")
async def authenticate_user(username: str, password: str, client_ip: str = None) -> Token:
    """사용자 인증 및 토큰 발급"""
    try:
        # 사용자 조회
        user_record = await get_user(
            username, 
            # password_manager.hash_password(password)
        )
            
        if not user_record:
            raise CustomError("USER_NOT_FOUND", username=username)
        
        if not user_record['is_active']:
            raise CustomError("INACTIVE_USER", username=username)
        
        # 비밀번호 검증
        if not password_manager.verify_password(
            password, 
            user_record['password_hash']
        ):
            raise CustomError("INVALID_CREDENTIALS")
    
        # JWT 토큰 생성
        token_data = jwt_manager.create_access_token(
            user_id=user_record['id'],
            username=user_record['username']
        )
            
        # Redis에 토큰 저장 (1시간 만료)
        await _store_token_in_redis(
            user_id=user_record['id'],
            token=token_data['access_token'],
            expire_seconds=3600  # 1시간
        )
            
        # 사용자 로그인 메트릭 추적
        track_user_login()
        
        # 🆕 로그인 IP 및 시간 업데이트
        if client_ip:
            # Repository 함수 사용
            success = await update_last_login(user_record['id'], client_ip)
            if success:
                logger.info(f"Updated login info for user {user_record['username']}", client_ip=client_ip)
            else:
                logger.warning(f"Failed to update login info for user {user_record['username']}", client_ip=client_ip)
            
            logger.info(
                f"User authenticated: {username}",
                client_ip=client_ip,
                user_id=user_record['id']
            )
            
            return Token(**token_data)
            
    except CustomError:
        raise
    except Exception as e:
        track_error("database_error", "/auth/login", "POST", None)
        logger.error(
            f"Authentication error: {e}",
            error_type="database_error",
            username=username
        )
        raise CustomError("DB_ERROR", "인증 중 데이터베이스 오류가 발생했습니다.")

@track_performance("token_verification")
async def verify_token_and_get_user(token: str) -> User | None:
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
        is_valid = await _verify_token_in_redis(user_id, token)
        if not is_valid:
            logger.warning(f"Token not found in Redis", user_id=user_id)
            return None
        
        # 사용자 조회
        user = await get_user_by_id(user_id)
        return user
        
    except Exception as e:
        track_error("token_verification_error", "/auth/verify", "GET", None)
        logger.error(f"Token verification error: {e}")
        return None

async def get_current_user_from_token(token: str) -> User:
    """토큰에서 현재 사용자 정보 추출 (서비스 레이어)"""
    try:
        user = await verify_token_and_get_user(token)
        if not user:
            raise CustomError("INVALID_TOKEN")
        return user
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Get current user error: {e}")
        raise CustomError("AUTH_ERROR", "사용자 인증 중 오류가 발생했습니다.")



# Redis 토큰 관리 헬퍼 함수들
async def _store_token_in_redis(user_id: int, token: str, expire_seconds: int = 3600):
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

async def _verify_token_in_redis(user_id: int, token: str) -> bool:
    """Redis에서 토큰 확인"""
    try:
        stored_token = await connections.redis.get(f"user_token:{user_id}:{token[-8:]}")
        return stored_token == token
    except Exception as e:
        logger.error(f"Failed to verify token in Redis: {e}", user_id=user_id)
        return False
