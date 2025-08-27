import json
import uuid
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
            import re
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
    
    # List Operations
    async def lpush(self, key: str, *values) -> int:
        """리스트 왼쪽에 추가"""
        redis = self.get_connection()
        return await redis.lpush(key, *values)
    
    async def rpush(self, key: str, *values) -> int:
        """리스트 오른쪽에 추가"""
        redis = self.get_connection()
        return await redis.rpush(key, *values)
    
    async def lpop(self, key: str) -> Any:
        """리스트 왼쪽에서 제거"""
        redis = self.get_connection()
        return await redis.lpop(key)
    
    async def rpop(self, key: str) -> Any:
        """리스트 오른쪽에서 제거"""
        redis = self.get_connection()
        return await redis.rpop(key)
    
    async def lrange(self, key: str, start: int = 0, end: int = -1) -> List[str]:
        """리스트 범위 조회"""
        redis = self.get_connection()
        return await redis.lrange(key, start, end)
    
    async def llen(self, key: str) -> int:
        """리스트 길이"""
        redis = self.get_connection()
        return await redis.llen(key)
    
    # Set Operations
    async def sadd(self, key: str, *members) -> int:
        """Set에 멤버 추가"""
        redis = self.get_connection()
        return await redis.sadd(key, *members)
    
    async def srem(self, key: str, *members) -> int:
        """Set에서 멤버 제거"""
        redis = self.get_connection()
        return await redis.srem(key, *members)
    
    async def smembers(self, key: str) -> set:
        """Set 모든 멤버 조회"""
        redis = self.get_connection()
        return await redis.smembers(key)
    
    async def sismember(self, key: str, member: str) -> bool:
        """Set 멤버 존재 확인"""
        redis = self.get_connection()
        return await redis.sismember(key, member)
    
    # Hash Operations
    async def hset(self, key: str, field: str, value: Any) -> int:
        """Hash 필드 설정"""
        redis = self.get_connection()
        if isinstance(value, (dict, list)):
            value = json.dumps(value, default=default_serializer)
        return await redis.hset(key, field, value)
    
    async def hget(self, key: str, field: str) -> Any:
        """Hash 필드 조회"""
        redis = self.get_connection()
        return await redis.hget(key, field)
    
    async def hgetall(self, key: str) -> Dict[str, Any]:
        """Hash 모든 필드 조회"""
        redis = self.get_connection()
        return await redis.hgetall(key)
    
    async def hdel(self, key: str, *fields) -> int:
        """Hash 필드 삭제"""
        redis = self.get_connection()
        return await redis.hdel(key, *fields)
    
    # Key Management - 실무 패턴 (scan 사용)
    async def scan(self, pattern: str = "*", count: int = 100) -> List[str]:
        """키 패턴 검색 (논블로킹)"""
        redis = self.get_connection()
        try:
            keys = []
            cursor = 0
            
            while True:
                cursor, batch_keys = await redis.scan(
                    cursor=cursor, 
                    match=pattern, 
                    count=count
                )
                keys.extend(batch_keys)
                
                if cursor == 0:  # 스캔 완료
                    break
            
            logger.debug(
                f"Scanned keys with pattern: {pattern}",
                operation="scan",
                pattern=pattern,
                found_count=len(keys)
            )
            return keys
            
        except Exception as e:
            logger.error(
                f"Failed to scan keys with pattern: {pattern}",
                operation="scan",
                pattern=pattern,
                error=str(e)
            )
            return []
    
    async def expire(self, key: str, seconds: int) -> bool:
        """키 만료 시간 설정"""
        redis = self.get_connection()
        return await redis.expire(key, seconds)
    
    # Pub/Sub Operations
    async def publish(self, channel: str, message: str) -> int:
        """메시지 발행"""
        redis = self.get_connection()
        return await redis.publish(channel, message)
    
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