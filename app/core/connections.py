from .logger import logger
from .monitor import collect_system_metrics
from .postgres_manager import postgres_manager
from .redis_manager import redis_manager
from .rmq_manager import rmq_manager


class ConnectionManager:
    """모든 외부 연결 관리자 (PostgreSQL + Redis + 기타) - 싱글톤 패턴"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def connect_all(self):
        """모든 외부 서비스 연결 초기화 (중복 초기화 방지)"""
        if self._initialized:
            logger.debug("Connections already initialized, skipping...")
            return
            
        try:
            # PostgreSQL 연결 풀 생성
            if not postgres_manager.pool:
                await postgres_manager.init(
                    min_size=5,
                    max_size=20,
                    command_timeout=60,
                    server_settings={'timezone': 'Asia/Seoul'}
                )
            
            # Redis 연결 풀 생성
            if not redis_manager.pool:
                await redis_manager.init(
                    max_connections=20,
                    socket_timeout=30,
                    socket_connect_timeout=10,
                    retry_on_timeout=True,
                    health_check_interval=30
                )
            
            # RabbitMQ 연결 (실패 시에도 계속 진행)
            if not rmq_manager.is_connected:
                rmq_success = rmq_manager.connect()
                if not rmq_success:
                    logger.warning(
                        "⚠️ RabbitMQ 연결 실패 - 메시지 큐 기능이 비활성화됩니다",
                        rmq_host=rmq_manager.connection.connection.params.host if rmq_manager.connection else "unknown",
                        rmq_port=rmq_manager.connection.connection.params.port if rmq_manager.connection else "unknown"
                    )
                else:
                    logger.info("✅ RabbitMQ 연결 성공")
            
            # 시스템 메트릭 자동 수집 시작
            collect_system_metrics()
            
            self._initialized = True
            logger.info("All database connections established successfully")
            
        except Exception as e:
            logger.error(f"Failed to establish connections: {e}", error_type="connection_error")
            raise
    
    async def disconnect_all(self):
        """모든 연결 종료"""
        await postgres_manager.close()
        await redis_manager.close()
        rmq_manager.disconnect()
        logger.info("All database connections closed")
    
    @property
    def postgres(self):
        """PostgreSQL 매니저 반환"""
        return postgres_manager
    
    @property
    def redis(self):
        """Redis 매니저 반환"""
        return redis_manager
    
    @property
    def rmq(self):
        """RabbitMQ 매니저 반환"""
        return rmq_manager
    
    async def health_check(self) -> dict:
        """모든 연결 상태 확인"""
        health_status = {
            "postgres": await postgres_manager.health_check(),
            "redis": await redis_manager.health_check(),
            "rmq": rmq_manager.is_connected,
            "rmq_workers": rmq_manager.get_worker_connection_info(),
            "overall": False
        }
        
        # 전체 상태 (워커 연결도 고려)
        rmq_healthy = health_status["rmq"] and len(health_status["rmq_workers"]) > 0
        health_status["overall"] = all([
            health_status["postgres"],
            health_status["redis"],
            rmq_healthy
        ])
        
        logger.debug(
            "Health check completed",
            postgres=health_status["postgres"],
            redis=health_status["redis"],
            rmq=health_status["rmq"],
            rmq_workers_count=len(health_status["rmq_workers"]),
            overall=health_status["overall"]
        )
        
        return health_status


# 전역 연결 관리자
connections = ConnectionManager()

# 간편한 import를 위한 별칭
postgres = connections.postgres
redis = connections.redis
rmq = connections.rmq

