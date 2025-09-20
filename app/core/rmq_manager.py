"""
RabbitMQ 연결 및 메시지 관리
"""
import json
import pika
import time
from typing import Dict, Any
from ..core.logger import logger
from ..core.config import settings
from ..core.monitor import track_api_call

class RMQManager:
    """RabbitMQ 연결 및 메시지 관리"""
    
    def __init__(self):
        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.channel.Channel | None = None
        self.is_connected = False
        
    def connect(self, max_retries: int = 3) -> bool:
        """RMQ 연결 (재시도 로직 포함)"""
        for attempt in range(max_retries):
            try:
                # 기존 연결 정리
                if self.connection and not self.connection.is_closed:
                    self.connection.close()
                
                # RabbitMQ 연결 설정
                credentials = pika.PlainCredentials(
                    username=settings.RMQ_USERNAME,
                    password=settings.RMQ_PASSWORD
                )
                
                parameters = pika.ConnectionParameters(
                    host=settings.RMQ_HOST,
                    port=settings.RMQ_PORT,
                    virtual_host=settings.RMQ_VHOST,
                    credentials=credentials,
                    heartbeat=600,  # 10분 heartbeat
                    blocked_connection_timeout=300,  # 5분 timeout
                    connection_attempts=3,
                    retry_delay=2
                )
                
                # 연결 생성
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                
                # 큐 선언
                self._declare_queues()
                
                self.is_connected = True
                logger.info(f"RMQ 연결 성공 (시도 {attempt + 1}/{max_retries})")
                return True
                
            except Exception as e:
                logger.warning(f"RMQ 연결 시도 {attempt + 1}/{max_retries} 실패: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 지수 백오프
                else:
                    logger.error(f"RMQ 연결 최종 실패: {e}")
                    return False
    
    def _declare_queues(self):
        """필요한 큐들 선언"""
        # 사용자 행동 로깅 큐
        self.channel.queue_declare(
            queue="user_actions",
            durable=True  # 서버 재시작 시에도 큐 유지
        )
        
        # 비즈니스 인사이트 업데이트 큐
        self.channel.queue_declare(
            queue="business_insights",
            durable=True
        )
        
        # DB 동기화 큐 (새로운)
        self.channel.queue_declare(
            queue="db_sync",
            durable=True
        )
        
        # 개인화 추천 업데이트 큐 (선택사항)
        self.channel.queue_declare(
            queue="personalization",
            durable=True
        )
    
    def publish_user_action(self, data: Dict[str, Any]) -> bool:
        """사용자 행동을 RMQ로 전송"""
        start_time = time.time()
        try:
            if not self.is_connected:
                self.connect()
            
            message = json.dumps(data, ensure_ascii=False, default=str)
            
            self.channel.basic_publish(
                exchange="",
                routing_key="user_actions",
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # 메시지 영속성
                    content_type="application/json"
                )
            )
            
            # 메트릭 추적
            duration = time.time() - start_time
            track_api_call("rabbitmq", "user_actions", 200, duration)
            
            logger.debug(f"사용자 행동 전송: {data.get('action', 'unknown')}")
            return True
            
        except Exception as e:
            duration = time.time() - start_time
            track_api_call("rabbitmq", "user_actions", 500, duration)
            logger.error(f"사용자 행동 전송 실패: {e}")
            return False
    
    def publish_business_insight(self, data: Dict[str, Any]) -> bool:
        """비즈니스 인사이트 업데이트를 RMQ로 전송"""
        try:
            if not self.is_connected:
                self.connect()
            
            message = json.dumps(data, ensure_ascii=False, default=str)
            
            self.channel.basic_publish(
                exchange="",
                routing_key="business_insights",
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json"
                )
            )
            
            logger.info("비즈니스 인사이트 업데이트 전송")
            return True
            
        except Exception as e:
            logger.error(f"비즈니스 인사이트 전송 실패: {e}")
            return False
    
    def publish_db_sync(self, data: Dict[str, Any]) -> bool:
        """DB 동기화 이벤트를 RMQ로 전송"""
        try:
            if not self.is_connected:
                self.connect()
            
            message = json.dumps(data, ensure_ascii=False, default=str)
            
            self.channel.basic_publish(
                exchange="",
                routing_key="db_sync",
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json"
                )
            )
            
            logger.debug(f"DB 동기화 이벤트 전송: {data.get('action', 'unknown')}")
            return True
            
        except Exception as e:
            logger.error(f"DB 동기화 이벤트 전송 실패: {e}")
            return False
    
    
    def disconnect(self):
        """RMQ 연결 해제"""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
            self.is_connected = False
            logger.info("RMQ 연결 해제")
        except Exception as e:
            logger.error(f"RMQ 연결 해제 실패: {e}")

# 싱글톤 인스턴스
rmq_manager = RMQManager()
