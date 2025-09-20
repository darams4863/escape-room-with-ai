"""
인증 관련 공통 의존성
여러 API에서 사용하는 인증 로직을 중앙화
"""

import json
from datetime import datetime
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..core.connections import redis_manager
from ..core.logger import logger
from ..core.exceptions import CustomError
from ..models.user import User
from ..core.monitor import track_performance, track_error
from ..utils.auth import jwt_manager
from ..repositories.user_repository import get_user_by_id


security = HTTPBearer()


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
        
    except CustomError:
        raise
    except Exception as e:
        track_error("token_verification_error", "/auth/verify", "GET", None)
        logger.error(f"Token verification error: {e}")
        return None


async def get_current_user_from_token(token: str) -> User:
    """토큰에서 현재 사용자 정보 추출"""
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """현재 인증된 사용자 반환 (FastAPI 의존성 주입용)
    
    여러 API 엔드포인트에서 공통으로 사용하는 인증 의존성
    JWT 토큰을 검증하고 사용자 정보를 반환합니다.
    
    Args:
        credentials: HTTPBearer에서 자동으로 추출된 인증 정보
        
    Returns:
        User: 인증된 사용자 정보
        
    Raises:
        HTTPException: 인증 실패 시 401 에러
    """
    try:
        return await get_current_user_from_token(credentials.credentials)
    except CustomError as e:
        logger.error(f"Authentication failed: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 처리 중 오류가 발생했습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        

# Redis 토큰 관리 헬퍼 함수 (통합 세션 구조)
async def _verify_token_in_redis(user_id: int, token: str) -> bool:
    """통합 세션에서 토큰 확인"""
    try:
        # 통합 세션에서 토큰 확인
        user_session_key = f"user_session:{user_id}"
        existing_session = await redis_manager.get(user_session_key)
        
        if not existing_session:
            return False
        
        session_data = json.loads(existing_session)
        stored_token = session_data.get("access_token")
        
        # 토큰 만료 시간 확인
        token_expires_at = session_data.get("token_expires_at")
        if token_expires_at:
            try:
                expires_at = datetime.fromisoformat(token_expires_at)
                if datetime.now() > expires_at:
                    logger.warning(f"Token expired for user {user_id}")
                    return False
            except ValueError:
                logger.warning(f"Invalid token expiration format for user {user_id}")
                return False
        
        return stored_token == token
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Failed to verify token in Redis: {e}", user_id=user_id)
        return False
