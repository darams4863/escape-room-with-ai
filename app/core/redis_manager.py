from datetime import datetime
import json
import re
import time
from typing import Any, Dict, Set
import uuid

from redis.asyncio import ConnectionPool, Redis

from ..utils.time import now_korea_iso
from .config import settings
from .logger import logger
from .monitor import track_redis_operation


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
            match = re.match(url_pattern, settings.REDIS_URL)
            
            if not match:
                raise ValueError(f"Invalid Redis URL format: {settings.REDIS_URL}")
            
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
    
    async def health_check(self) -> bool:
        """Redis 연결 상태 확인"""
        try:
            redis = self.get_connection()
            await redis.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False
    
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
        value: str | int | float | dict | list,
        ex: int | None = None,
        nx: bool = False
    ) -> bool:
        """값 설정"""
        start_time = time.time()
        redis = self.get_connection()
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, default=default_serializer)
            
            if ex is not None and ex > 0:
                result = await redis.setex(key, ex, value)
            else:
                result = await redis.set(key, value, nx=nx)
            
            duration = (time.time() - start_time) * 1000
            track_redis_operation("redis_set", duration, True)
            
            logger.debug(
                f"Redis SET: {key}",
                operation="set",
                key=key,
                ttl=ex,
                nx=nx
            )
            return bool(result)
            
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            track_redis_operation("redis_set", duration, False)
            
            logger.error(
                f"Failed to set Redis key: {key}",
                operation="set",
                key=key,
                error=str(e)
            )
            return False
    
    async def get(self, key: str) -> Any:
        """값 조회"""
        start_time = time.time()
        redis = self.get_connection()
        try:
            value = await redis.get(key)
            duration = (time.time() - start_time) * 1000
            track_redis_operation("redis_get", duration, True)
            
            logger.debug(
                f"Redis GET: {key}",
                operation="get",
                key=key,
                found=value is not None
            )
            return value
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            track_redis_operation("redis_get", duration, False)
            
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
    
    # Key Management
    async def expire(self, key: str, seconds: int) -> bool:
        """키 만료 시간 설정 (실무에서 가끔 필요)"""
        redis = self.get_connection()
        try:
            result = await redis.expire(key, seconds)
            logger.debug(f"Redis EXPIRE: {key} -> {seconds}s")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to expire key {key}: {e}")
            return False
    
    # =============================================================================
    # 실무에서 자주 사용되는 Redis 패턴들
    # =============================================================================
    
    async def mget(self, keys: list[str]) -> list[Any]:
        """여러 키를 한 번에 조회 (성능 최적화)"""
        redis = self.get_connection()
        try:
            values = await redis.mget(keys)
            return [json.loads(v) if v and v.startswith('{') else v for v in values]
        except Exception as e:
            logger.error(f"Failed to mget keys: {e}")
            return [None] * len(keys)
    
    async def mset(self, mapping: dict[str, Any], ttl: int | None = None) -> bool:
        """여러 키를 한 번에 설정 (성능 최적화)"""
        redis = self.get_connection()
        try:
            # JSON 직렬화
            serialized = {}
            for key, value in mapping.items():
                if isinstance(value, (dict, list)):
                    serialized[key] = json.dumps(value, default=default_serializer)
                else:
                    serialized[key] = value
            
            result = await redis.mset(serialized)
            
            # TTL 설정
            if ttl and ttl > 0:
                for key in mapping.keys():
                    await redis.expire(key, ttl)
            
            return result
        except Exception as e:
            logger.error(f"Failed to mset keys: {e}")
            return False
    
    async def incr(self, key: str, amount: int = 1) -> int:
        """카운터 증가 (조회수, 좋아요 등)"""
        redis = self.get_connection()
        try:
            return await redis.incrby(key, amount)
        except Exception as e:
            logger.error(f"Failed to increment key {key}: {e}")
            return 0
    
    async def decr(self, key: str, amount: int = 1) -> int:
        """카운터 감소"""
        redis = self.get_connection()
        try:
            return await redis.decrby(key, amount)
        except Exception as e:
            logger.error(f"Failed to decrement key {key}: {e}")
            return 0
    
    async def hset(self, name: str, mapping: dict[str, Any]) -> int:
        """해시 필드 설정 (사용자 프로필, 설정 등)"""
        redis = self.get_connection()
        try:
            # JSON 직렬화
            serialized = {}
            for field, value in mapping.items():
                if isinstance(value, (dict, list)):
                    serialized[field] = json.dumps(value, default=default_serializer)
                else:
                    serialized[field] = str(value)
            
            return await redis.hset(name, mapping=serialized)
        except Exception as e:
            logger.error(f"Failed to hset {name}: {e}")
            return 0
    
    async def hget(self, name: str, field: str) -> Any:
        """해시 필드 조회"""
        redis = self.get_connection()
        try:
            value = await redis.hget(name, field)
            if value and value.startswith('{'):
                return json.loads(value)
            return value
        except Exception as e:
            logger.error(f"Failed to hget {name}.{field}: {e}")
            return None
    
    async def hgetall(self, name: str) -> dict[str, Any]:
        """해시 전체 조회"""
        redis = self.get_connection()
        try:
            data = await redis.hgetall(name)
            result = {}
            for field, value in data.items():
                if value and value.startswith('{'):
                    result[field] = json.loads(value)
                else:
                    result[field] = value
            return result
        except Exception as e:
            logger.error(f"Failed to hgetall {name}: {e}")
            return {}
    
    async def lpush(self, name: str, *values: Any) -> int:
        """리스트 앞에 추가 (최근 활동, 로그 등)"""
        redis = self.get_connection()
        try:
            serialized = [json.dumps(v, default=default_serializer) if isinstance(v, (dict, list)) else str(v) for v in values]
            return await redis.lpush(name, *serialized)
        except Exception as e:
            logger.error(f"Failed to lpush {name}: {e}")
            return 0
    
    async def lrange(self, name: str, start: int = 0, end: int = -1) -> list[Any]:
        """리스트 조회 (최근 N개 항목)"""
        redis = self.get_connection()
        try:
            values = await redis.lrange(name, start, end)
            result = []
            for value in values:
                if value and value.startswith('{'):
                    result.append(json.loads(value))
                else:
                    result.append(value)
            return result
        except Exception as e:
            logger.error(f"Failed to lrange {name}: {e}")
            return []
    
    async def sadd(self, name: str, *values: Any) -> int:
        """셋에 추가 (중복 제거, 태그 등)"""
        redis = self.get_connection()
        try:
            serialized = [str(v) for v in values]
            return await redis.sadd(name, *serialized)
        except Exception as e:
            logger.error(f"Failed to sadd {name}: {e}")
            return 0
    
    async def smembers(self, name: str) -> Set[str]:
        """셋 조회"""
        redis = self.get_connection()
        try:
            return await redis.smembers(name)
        except Exception as e:
            logger.error(f"Failed to smembers {name}: {e}")
            return set()
    
    # =============================================================================
    # Rate Limiting & Caching (단순 메서드)
    # =============================================================================
    
    async def cache_user_preferences(self, user_id: int, preferences: Dict[str, Any], ttl: int = 3600) -> bool:
        """사용자 선호도 캐시 저장 (통합 세션 구조)"""
        try:
            # 통합 세션에 선호도 저장
            user_session_key = f"user_session:{user_id}"
            existing_session = await self.get(user_session_key)
            
            if existing_session:
                session_data = json.loads(existing_session)
                session_data["preferences"] = preferences
                session_data["preferences_updated_at"] = now_korea_iso()
                
                await self.set(
                    key=user_session_key,
                    value=json.dumps(session_data, ensure_ascii=False),
                    ex=ttl
                )
                logger.debug(f"Preferences cached in unified session: {user_id}")
                return True
            else:
                # 세션이 없으면 별도 키로 저장 (폴백)
                key = f"user_preferences:{user_id}"
                await self.set(key, json.dumps(preferences, ensure_ascii=False), ttl)
                logger.debug(f"Cache set for user preferences (fallback): {user_id}")
                return True
        except Exception as e:
            logger.error(f"Cache set error: {e}", user_id=user_id)
            return False
    
    async def get_cached_user_preferences(self, user_id: int) -> Dict[str, Any] | None:
        """사용자 선호도 캐시 조회 (통합 세션 구조)"""
        try:
            # 통합 세션에서 선호도 조회
            user_session_key = f"user_session:{user_id}"
            existing_session = await self.get(user_session_key)
            
            if existing_session:
                session_data = json.loads(existing_session)
                preferences = session_data.get("preferences")
                if preferences:
                    logger.debug(f"Cache hit for user preferences (unified): {user_id}")
                    return preferences
            
            # 폴백: 별도 키에서 조회
            key = f"user_preferences:{user_id}"
            cached_data = await self.get(key)
            
            if cached_data:
                logger.debug(f"Cache hit for user preferences (fallback): {user_id}")
                return json.loads(cached_data)
            
            logger.debug(f"Cache miss for user preferences: {user_id}")
            return None
            
        except Exception as e:
            logger.error(f"Cache get error: {e}", user_id=user_id)
            return None
    



# 전역 Redis 관리자
redis_manager = RedisManager()