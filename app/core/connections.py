from .logger import logger
from .postgres_manager import postgres_manager
from .redis_manager import redis_manager
from .rmq_manager import rmq_manager
from .monitor import collect_system_metrics


class ConnectionManager:
    """모든 외부 연결 관리자 (PostgreSQL + Redis + 기타)"""
    
    async def connect_all(self):
        """모든 외부 서비스 연결 초기화"""
        try:
            # PostgreSQL 연결 풀 생성
            await postgres_manager.init(
                min_size=5,
                max_size=20,
                command_timeout=60,
                server_settings={'timezone': 'Asia/Seoul'}
            )
            
            # Redis 연결 풀 생성
            await redis_manager.init(
                max_connections=20,
                socket_timeout=30,
                socket_connect_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            # RabbitMQ 연결
            rmq_manager.connect()
            
            # 시스템 메트릭 자동 수집 시작
            collect_system_metrics()
            
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
            "redis": await redis_manager.ping(),
            "rmq": rmq_manager.is_connected,
            "overall": False
        }
        
        # 전체 상태
        health_status["overall"] = all([
            health_status["postgres"],
            health_status["redis"],
            health_status["rmq"]
        ])
        
        logger.debug(
            "Health check completed",
            postgres=health_status["postgres"],
            redis=health_status["redis"],
            rmq=health_status["rmq"],
            overall=health_status["overall"]
        )
        
        return health_status


# 전역 연결 관리자
connections = ConnectionManager()

# 간편한 import를 위한 별칭
postgres = connections.postgres
redis = connections.redis
rmq = connections.rmq

