from datetime import datetime, timedelta
from typing import Any, Dict

import bcrypt
import jwt

from ..core.config import settings
from ..core.logger import logger
from .time import now_korea


class PasswordManager:
    """비밀번호 해싱 및 검증 관리자"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """비밀번호를 bcrypt로 해싱"""
        try:
            # 솔트 생성 및 해싱
            salt = bcrypt.gensalt()
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            return hashed.decode('utf-8')
        except Exception as e:
            logger.error(f"Password hashing error: {e}")
            raise
    
    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        """비밀번호 검증"""
        try:
            return bcrypt.checkpw(
                password.encode('utf-8'), 
                hashed_password.encode('utf-8')
            )
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False


class JWTManager:
    """JWT 토큰 관리자"""
    
    @staticmethod
    def create_access_token(user_id: int, username: str) -> Dict[str, Any]:
        """JWT 액세스 토큰 생성"""
        try:
            # 토큰 만료 시간 계산
            expire = now_korea() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
            
            # 페이로드 생성
            payload = {
                "user_id": user_id,
                "username": username,
                "exp": expire,
                "iat": now_korea()
            }
            
            # JWT 토큰 생성
            token = jwt.encode(
                payload, 
                settings.JWT_SECRET_KEY, 
                algorithm=settings.JWT_ALGORITHM
            )
            
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": settings.JWT_EXPIRE_HOURS * 3600  # 초 단위
            }
            
        except Exception as e:
            logger.error(f"Token creation error: {e}")
            raise
    
    @staticmethod
    def verify_token(token: str) -> Dict[str, Any] | None:
        """JWT 토큰 검증 및 페이로드 반환"""
        try:
            # 토큰 디코딩
            payload = jwt.decode(
                token, 
                settings.JWT_SECRET_KEY, 
                algorithms=[settings.JWT_ALGORITHM]
            )
            
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
        except Exception as e:
            logger.error(f"Token verification error: {e}")
            return None
    
    @staticmethod
    def extract_token_from_header(authorization: str) -> str | None:
        """Authorization 헤더에서 토큰 추출"""
        try:
            if not authorization:
                return None
            
            # Bearer 토큰 형식 확인
            parts = authorization.split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                return None
            
            return parts[1]
            
        except Exception as e:
            logger.error(f"Token extraction error: {e}")
            return None


password_manager = PasswordManager()
jwt_manager = JWTManager()
