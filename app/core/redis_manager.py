import re 
import json
import uuid
import hashlib
import time 
from datetime import datetime
from typing import Any, Dict, List, Union

from redis.asyncio import ConnectionPool, Redis

from .config import settings
from .logger import logger


def default_serializer(obj):
    """JSON 직렬화를 위한 기본 시리얼라이저"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


class RedisManager:
    """Redis 연결 풀 관리자 - 실무 패턴"""
    
    def __init__(self):
        self.pool: ConnectionPool | None = None
        self.connection_id: str | None = None
        
    async def init(
        self,
        max_connections: int = 20,
        socket_timeout: int = 30,
        socket_connect_timeout: int = 10,
        retry_on_timeout: bool = True,
        health_check_interval: int = 30
    ):
        """Redis 연결 풀 초기화"""
        try:
            self.connection_id = str(uuid.uuid4())[:8]
            
            # Redis URL에서 정보 추출
            url_pattern = r'redis://(?:([^:]*):([^@]*)@)?([^:]*):(\d+)(?:/(\d+))?'
            match = re.match(url_pattern, settings.redis_url)
            
            if not match:
                raise ValueError(f"Invalid Redis URL format: {settings.redis_url}")
            
            username, password, host, port, db = match.groups()
            
            self.pool = ConnectionPool(
                host=host,
                port=int(port),
                db=int(db) if db else 0,
                password=password,
                username=username,
                max_connections=max_connections,
                decode_responses=True,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                retry_on_timeout=retry_on_timeout,
                health_check_interval=health_check_interval
            )
            
            # 연결 테스트
            redis = self.get_connection()
            await redis.ping()
            
            logger.info(
                "Redis connection pool initialized",
                connection_id=self.connection_id,
                host=host,
                port=port,
                db=db,
                max_connections=max_connections
            )
            
        except Exception as e:
            logger.error(
                f"Failed to initialize Redis pool: {e}",
                error_type="redis_init_error"
            )
            raise
    
    def get_connection(self) -> Redis:
        """Redis 연결 획득 - 실무 패턴"""
        if not self.pool:
            raise RuntimeError("Redis pool not initialized. Call init() first.")
        
        return Redis(
            connection_pool=self.pool,
            decode_responses=True
        )
    
    def get_pipeline(self, transaction: bool = True):
        """Redis 파이프라인 생성"""
        redis = self.get_connection()
        return redis.pipeline(transaction=transaction)
    
    async def close(self):
        """연결 풀 종료"""
        if self.pool:
            try:
                await self.pool.aclose()
                logger.info(
                    "Redis connection pool closed",
                    connection_id=self.connection_id
                )
            except Exception as e:
                logger.error(
                    f"Error closing Redis pool: {e}",
                    connection_id=self.connection_id,
                    error_type="redis_close_error"
                )
    
    # Basic CRUD Operations - 실무 패턴
    async def set(
        self,
        key: str,
        value: Union[str, int, float, dict, list],
        ex: int | None = None,
        nx: bool = False
    ) -> bool:
        """값 설정"""
        redis = self.get_connection()
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, default=default_serializer)
            
            if ex is not None and ex > 0:
                result = await redis.setex(key, ex, value)
            else:
                result = await redis.set(key, value, nx=nx)
            
            logger.debug(
                f"Redis SET: {key}",
                operation="set",
                key=key,
                ttl=ex,
                nx=nx
            )
            return bool(result)
            
        except Exception as e:
            logger.error(
                f"Failed to set Redis key: {key}",
                operation="set",
                key=key,
                error=str(e)
            )
            return False
    
    async def get(self, key: str) -> Any:
        """값 조회"""
        redis = self.get_connection()
        try:
            value = await redis.get(key)
            logger.debug(
                f"Redis GET: {key}",
                operation="get",
                key=key,
                found=value is not None
            )
            return value
        except Exception as e:
            logger.error(
                f"Failed to get Redis key: {key}",
                operation="get",
                key=key,
                error=str(e)
            )
            return None
    
    async def delete(self, key: str) -> int:
        """키 삭제"""
        redis = self.get_connection()
        try:
            result = await redis.delete(key)
            logger.debug(
                f"Redis DELETE: {key}",
                operation="delete",
                key=key,
                deleted=result
            )
            return result
        except Exception as e:
            logger.error(
                f"Failed to delete Redis key: {key}",
                operation="delete",
                key=key,
                error=str(e)
            )
            return 0
    
    async def exists(self, key: str) -> bool:
        """키 존재 확인"""
        redis = self.get_connection()
        return bool(await redis.exists(key))
    
    async def ttl(self, key: str) -> int:
        """TTL 조회"""
        redis = self.get_connection()
        return await redis.ttl(key)
    
    # Key Management
    async def expire(self, key: str, seconds: int) -> bool:
        """키 만료 시간 설정"""
        redis = self.get_connection()
        return await redis.expire(key, seconds)
    
    # =============================================================================
    # Rate Limiting & Caching (단순 메서드)
    # =============================================================================
    
    async def rate_limit_check(self, user_id: int, limit: int = 10, window: int = 60) -> tuple[bool, Dict[str, int]]:
        """사용자별 Rate Limiting 체크"""
        try:
            current_time = int(time.time())
            window_start = current_time - window
            
            # Redis 키 생성
            key = f"rate_limit:{user_id}"
            
            # 현재 윈도우의 요청 수 조회
            redis = self.get_connection()
            pipe = redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)  # 오래된 요청 제거
            pipe.zcard(key)  # 현재 요청 수 조회
            pipe.zadd(key, {str(current_time): current_time})  # 현재 요청 추가
            pipe.expire(key, window)  # TTL 설정
            
            results = await pipe.execute()
            current_requests = results[1]
            
            # 제한 확인
            is_allowed = current_requests < limit
            
            # 상태 정보
            status = {
                "current_requests": current_requests + 1,
                "limit": limit,
                "window": window,
                "remaining": max(0, limit - current_requests - 1),
                "reset_time": current_time + window
            }
            
            if not is_allowed:
                logger.warning(
                    f"Rate limit exceeded for user {user_id}",
                    user_id=user_id,
                    current_requests=current_requests,
                    limit=limit
                )
            
            return is_allowed, status
            
        except Exception as e:
            logger.error(f"Rate limiter error: {e}", user_id=user_id)
            # Redis 오류 시 허용 (fail-open)
            return True, {"error": "rate_limiter_unavailable"}
    
    async def cache_user_preferences(self, user_id: int, preferences: Dict[str, Any], ttl: int = 3600) -> bool:
        """사용자 선호도 캐시 저장"""
        try:
            key = f"user_preferences:{user_id}"
            await self.set(key, json.dumps(preferences, ensure_ascii=False), ttl)
            logger.debug(f"Cache set for user preferences: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}", user_id=user_id)
            return False
    
    async def get_cached_user_preferences(self, user_id: int) -> Dict[str, Any] | None:
        """사용자 선호도 캐시 조회"""
        try:
            key = f"user_preferences:{user_id}"
            cached_data = await self.get(key)
            
            if cached_data:
                logger.debug(f"Cache hit for user preferences: {user_id}")
                return json.loads(cached_data)
            
            logger.debug(f"Cache miss for user preferences: {user_id}")
            return None
            
        except Exception as e:
            logger.error(f"Cache get error: {e}", user_id=user_id)
            return None
    
    async def invalidate_user_preferences(self, user_id: int) -> bool:
        """사용자 선호도 캐시 무효화"""
        try:
            key = f"user_preferences:{user_id}"
            await self.delete(key)
            logger.debug(f"Cache invalidated for user preferences: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}", user_id=user_id)
            return False
    
    async def cache_recommendations(self, cache_key: str, recommendations: list, ttl: int = 1800) -> bool:
        """방탈출 추천 결과 캐시 저장 (30분)"""
        try:
            key = f"recommendations:{cache_key}"
            await self.set(key, json.dumps(recommendations, ensure_ascii=False, default=str), ttl)
            logger.debug(f"Cache set for recommendations: {cache_key}")
            return True
        except Exception as e:
            logger.error(f"Cache set error for recommendations: {e}")
            return False
    
    async def get_cached_recommendations(self, cache_key: str) -> list | None:
        """방탈출 추천 결과 캐시 조회"""
        try:
            key = f"recommendations:{cache_key}"
            cached_data = await self.get(key)
            
            if cached_data:
                logger.debug(f"Cache hit for recommendations: {cache_key}")
                return json.loads(cached_data)
            
            return None
            
        except Exception as e:
            logger.error(f"Cache get error for recommendations: {e}")
            return None
    
    def generate_recommendation_cache_key(self, user_message: str, user_prefs: Dict[str, Any]) -> str:
        """추천 결과 캐시 키 생성"""
        # 사용자 메시지와 선호도 기반으로 캐시 키 생성
        key_data = {
            "message": user_message.lower().strip(),
            "preferences": {
                "experience_level": user_prefs.get("experience_level"),
                "preferred_difficulty": user_prefs.get("preferred_difficulty"),
                "preferred_regions": sorted(user_prefs.get("preferred_regions", [])),
                "preferred_themes": sorted(user_prefs.get("preferred_themes", [])),
                "preferred_group_size": user_prefs.get("preferred_group_size")
            }
        }
        
        key_string = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(key_string.encode()).hexdigest()

   
    # Utility Methods
    async def ping(self) -> bool:
        """연결 상태 확인"""
        try:
            redis = self.get_connection()
            await redis.ping()
            return True
        except Exception:
            return False


# 전역 Redis 관리자
redis_manager = RedisManager()