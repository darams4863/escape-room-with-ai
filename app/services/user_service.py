"""사용자 관련 비즈니스 로직 (함수 기반)"""

from datetime import datetime, timedelta
import json
from typing import Dict

from ..core.connections import redis_manager
from ..core.exceptions import CustomError
from ..core.logger import logger
from ..core.monitor import (
    track_error,
    track_performance,
    track_user_login,
    track_user_registration,
)
from ..models.user import Token, User
from ..repositories.chat_repository import get_latest_session_by_user_id
from ..repositories.user_repository import get_user, insert_user, update_last_login
from ..services.chat_service import get_or_create_user_session
from ..utils.auth import jwt_manager, password_manager
from ..utils.time import now_korea_iso


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
            
        # Redis에 토큰 저장 (1시간 만료) + 세션 복원
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


# Redis 토큰 관리 헬퍼 함수 (통합 세션 구조)
async def _store_token_in_redis(user_id: int, token: str, expire_seconds: int = 3600):
    """통합 세션에 토큰 저장 (기존 토큰 삭제)"""
    try:
        # 1. 기존 토큰 삭제
        await _invalidate_existing_tokens(user_id)
        
        # 2. 통합 세션에 토큰 저장
        user_session_key = f"user_session:{user_id}"
        existing_session = await redis_manager.get(user_session_key)
        
        if existing_session:
            # 기존 세션이 있으면 토큰만 업데이트
            session_data = json.loads(existing_session)
            session_data["access_token"] = token
            current_time = datetime.now()
            session_data["token_expires_at"] = (current_time + timedelta(seconds=expire_seconds)).isoformat()
            
            await redis_manager.set(
                key=user_session_key,
                value=json.dumps(session_data, ensure_ascii=False),
                ex=expire_seconds
            )
            
            logger.info(f"Token updated in existing session: {user_id}")
        else:
            # 세션이 없으면 DB에서 기존 세션 복원 시도
            session_data = await _restore_session_from_db(user_id)
            
            if session_data:
                # DB에서 복원된 세션에 토큰 추가
                session_data["access_token"] = token
                current_time = datetime.now()
                session_data["token_expires_at"] = (current_time + timedelta(seconds=expire_seconds)).isoformat()
                
                await redis_manager.set(
                    key=user_session_key,
                    value=json.dumps(session_data, ensure_ascii=False),
                    ex=expire_seconds
                )
                
                logger.info(f"Token stored in restored session: {user_id}")
            else:
                # DB에도 세션이 없으면 새로 생성
                await get_or_create_user_session(user_id)
                
                # 다시 시도
                existing_session = await redis_manager.get(user_session_key)
                if existing_session:
                    session_data = json.loads(existing_session)
                    session_data["access_token"] = token
                    current_time = datetime.now()
                    session_data["token_expires_at"] = (current_time + timedelta(seconds=expire_seconds)).isoformat()
                    
                    await redis_manager.set(
                        key=user_session_key,
                        value=json.dumps(session_data, ensure_ascii=False),
                        ex=expire_seconds
                    )
                    
                    logger.info(f"Token stored in new session: {user_id}")
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Failed to store token in Redis: {e}", user_id=user_id)
        raise

async def _restore_session_from_db(user_id: int) -> Dict | None:
    """DB에서 사용자의 최신 세션을 조회하여 세션 데이터 반환"""
    try:
        # DB에서 최신 세션 조회
        latest_session = await get_latest_session_by_user_id(user_id)
        
        if latest_session:
            # DB 세션 데이터를 Redis 형식으로 변환
            session_data = {
                "session_id": latest_session["session_id"],
                "user_id": user_id,
                "created_at": latest_session["created_at"].isoformat() if latest_session["created_at"] else now_korea_iso(),
                "messages": json.loads(latest_session["conversation_history"]).get("messages", []),
                "last_activity": latest_session["updated_at"].isoformat() if latest_session["updated_at"] else now_korea_iso()
            }
            
            logger.info(f"Found existing session in DB for user {user_id}: {latest_session['session_id']}")
            return session_data
        else:
            logger.debug(f"No existing session found in DB for user {user_id}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to restore session from DB: {e}", user_id=user_id)
        return None

async def _invalidate_existing_tokens(user_id: int):
    """기존 토큰 무효화"""
    try:
        # 통합 세션에서 토큰 제거
        user_session_key = f"user_session:{user_id}"
        existing_session = await redis_manager.get(user_session_key)
        
        if existing_session:
            session_data = json.loads(existing_session)
            if "access_token" in session_data:
                del session_data["access_token"]
                del session_data["token_expires_at"]
                
                await redis_manager.set(
                    key=user_session_key,
                    value=json.dumps(session_data, ensure_ascii=False),
                    ex=86400
                )
                
    except Exception as e:
        logger.error(f"Failed to invalidate existing tokens: {e}", user_id=user_id)
