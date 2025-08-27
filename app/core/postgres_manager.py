import re
import asyncio
import traceback
from contextlib import asynccontextmanager
from typing import Dict, Any
import uuid

import asyncpg

from .config import settings
from .logger import logger


class PostgresManager:
    """PostgreSQL 연결 풀 관리자"""
    
    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.connection_id: str | None = None
        
    async def init(
        self,
        min_size: int = 5,
        max_size: int = 20,
        command_timeout: int = 60,
        server_settings: Dict[str, str] | None = None
    ):
        """PostgreSQL 연결 풀 초기화"""
        try:
            self.connection_id = str(uuid.uuid4())[:8]
            
            # URL에서 연결 정보 파싱
            url_pattern = r'postgresql://(?:([^:]*):([^@]*)@)?([^:/]*):?(\d*)/([^?]*)'
            match = re.match(url_pattern, settings.database_url)
            
            if not match:
                raise ValueError(f"Invalid PostgreSQL URL format: {settings.database_url}")
            
            username, password, host, port, database = match.groups()
            
            # 기본값 설정
            # port = int(port) if port else 5432
            port = int(port) if port else 5433
            server_settings = server_settings or {'timezone': 'Asia/Seoul'}
            
            self.pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=username,
                password=password,
                min_size=min_size,
                max_size=max_size,
                command_timeout=command_timeout,
                server_settings=server_settings
            )
            
            # 연결 테스트
            async with self.get_connection() as conn:
                await conn.fetchval("SELECT 1")
            
            logger.info(
                "PostgreSQL connection pool created",
                connection_id=self.connection_id,
                db_host=host,
                db_port=port,
                database=database,
                min_size=min_size,
                max_size=max_size,
                service="postgresql"
            )
            
        except Exception as e:
            logger.error(
                f"Failed to create PostgreSQL pool: {e}",
                connection_id=self.connection_id,
                db_host=host,
                db_port=port,
                database=database,
                error_type=type(e).__name__,
                service="postgresql"
            )
            raise
    
    @asynccontextmanager
    async def get_connection(self):
        """컨텍스트 매니저로 안전한 연결 관리"""
        if not self.pool:
            raise RuntimeError("PostgreSQL pool not initialized. Call init() first.")
        
        try:
            async with self.pool.acquire() as conn:
                yield conn
                
        except (
            ConnectionResetError,
            ConnectionRefusedError,
            StopAsyncIteration,
            GeneratorExit,
            asyncpg.exceptions.InterfaceError
        ) as e:
            logger.error(
                f"PostgreSQL connection error: {e}",
                error_type="postgres_connection_error",
                connection_id=self.connection_id
            )
            
            # 자동 재연결 시도
            await self._attempt_reconnection()
            
            # 재연결 후 다시 시도
            async with self.pool.acquire() as conn:
                yield conn
                
        except Exception as e:
            logger.error(
                f"Unexpected PostgreSQL error: {e}",
                error_type="postgres_unexpected_error",
                connection_id=self.connection_id,
                traceback=traceback.format_exc()
            )
            raise
    
    @asynccontextmanager
    async def get_transaction(self):
        """트랜잭션 컨텍스트 매니저"""
        async with self.get_connection() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                yield conn
                await tr.commit()
                logger.debug(
                    "Transaction committed",
                    operation="transaction_commit",
                    connection_id=self.connection_id
                )
            except Exception as e:
                await tr.rollback()
                logger.warning(
                    f"Transaction rolled back: {e}",
                    operation="transaction_rollback",
                    connection_id=self.connection_id,
                    error=str(e)
                )
                raise
    
    async def _attempt_reconnection(self, max_retries: int = 3):
        """자동 재연결 시도"""
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"Attempting PostgreSQL reconnection ({attempt}/{max_retries})",
                    connection_id=self.connection_id,
                    attempt=attempt
                )
                
                # 기존 풀 정리
                if self.pool:
                    await self.pool.close()
                
                # 새 풀 생성
                await self.init()
                
                logger.info(
                    "PostgreSQL reconnection successful",
                    connection_id=self.connection_id,
                    attempt=attempt
                )
                return
                
            except Exception as e:
                retry_delay = 2 ** attempt  # 지수적 백오프
                logger.warning(
                    f"PostgreSQL reconnection attempt {attempt} failed: {e}",
                    connection_id=self.connection_id,
                    attempt=attempt,
                    retry_delay=retry_delay,
                    error=str(e)
                )
                
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                else:
                    logger.critical(
                        f"PostgreSQL reconnection failed after {max_retries} attempts",
                        connection_id=self.connection_id,
                        max_retries=max_retries
                    )
                    raise
    
    async def close(self):
        """연결 풀 종료"""
        if self.pool:
            try:
                await self.pool.close()
                logger.info(
                    "PostgreSQL connection pool closed",
                    connection_id=self.connection_id,
                    service="postgresql"
                )
            except Exception as e:
                logger.error(
                    f"Error closing PostgreSQL pool: {e}",
                    connection_id=self.connection_id,
                    error_type="postgres_close_error"
                )
    
    async def health_check(self) -> bool:
        """연결 상태 확인"""
        try:
            async with self.get_connection() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.warning(
                f"PostgreSQL health check failed: {e}",
                connection_id=self.connection_id,
                service="postgresql"
            )
            return False
    
    # 편의 메소드들
    async def execute(self, query: str, *args) -> str:
        """단일 쿼리 실행"""
        async with self.get_connection() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> list:
        """다중 행 조회"""
        async with self.get_connection() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        """단일 행 조회"""
        async with self.get_connection() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args) -> Any:
        """단일 값 조회"""
        async with self.get_connection() as conn:
            return await conn.fetchval(query, *args)
    
    async def executemany(self, query: str, args_list: list) -> None:
        """배치 실행"""
        async with self.get_connection() as conn:
            await conn.executemany(query, args_list)
    
    # 트랜잭션 편의 메소드들
    async def execute_in_transaction(self, query: str, *args) -> str:
        """트랜잭션 내에서 쿼리 실행"""
        async with self.get_transaction() as conn:
            return await conn.execute(query, *args)
    
    async def fetch_in_transaction(self, query: str, *args) -> list:
        """트랜잭션 내에서 다중 행 조회"""
        async with self.get_transaction() as conn:
            return await conn.fetch(query, *args)
    
    async def batch_insert(self, table: str, columns: list, data: list) -> None:
        """배치 인서트 (트랜잭션)"""
        placeholders = ','.join([f'${i+1}' for i in range(len(columns))])
        query = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        
        async with self.get_transaction() as conn:
            await conn.executemany(query, data)
            
        logger.info(
            f"Batch insert completed",
            table=table,
            columns=columns,
            row_count=len(data),
            operation="batch_insert"
        )


# 전역 PostgreSQL 매니저
postgres_manager = PostgresManager()
