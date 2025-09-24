"""
RabbitMQ 연결 및 메시지 관리
"""
import json
import time
from typing import Any, Dict

import pika

from ..core.config import settings
from ..core.logger import logger
from ..core.monitor import track_api_call


class RMQManager:
    """RabbitMQ 연결 및 메시지 관리"""
    
    def __init__(self):
        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.channel.Channel | None = None
        self.is_connected = False
        self._worker_connections = {}  # 워커별 연결 관리
        
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
                
                connection_params = pika.ConnectionParameters(
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
                self.connection = pika.BlockingConnection(connection_params)
                self.channel = self.connection.channel()
                
                # 큐 선언
                self._declare_queues()
                
                self.is_connected = True
                logger.info(f"✅ RMQ 연결 성공 (시도 {attempt + 1}/{max_retries}) - Host: {settings.RMQ_HOST}:{settings.RMQ_PORT}")
                return True
                
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                
                # 연결 실패 원인 분석
                if "Connection refused" in error_msg:
                    reason = "RabbitMQ 서버가 실행되지 않음"
                elif "Authentication failed" in error_msg:
                    reason = "인증 실패 (사용자명/비밀번호 확인)"
                elif "timeout" in error_msg.lower():
                    reason = "연결 타임아웃 (네트워크 또는 서버 응답 지연)"
                elif "Name or service not known" in error_msg:
                    reason = "호스트명을 찾을 수 없음"
                elif "Connection reset" in error_msg:
                    reason = "연결이 서버에 의해 리셋됨"
                else:
                    reason = "알 수 없는 연결 오류"
                
                logger.warning(
                    f"RMQ 연결 시도 {attempt + 1}/{max_retries} 실패: {reason}",
                    error_type=error_type,
                    error_message=error_msg,
                    host=settings.RMQ_HOST,
                    port=settings.RMQ_PORT,
                    username=settings.RMQ_USERNAME
                )
                
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 지수 백오프
                else:
                    logger.error(
                        f"RMQ 연결 최종 실패: {reason}",
                        error_type=error_type,
                        error_message=error_msg,
                        host=settings.RMQ_HOST,
                        port=settings.RMQ_PORT,
                        username=settings.RMQ_USERNAME,
                        total_attempts=max_retries
                    )
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
        
        # DB 동기화 큐 
        self.channel.queue_declare(
            queue="db_sync",
            durable=True
        )
        
        # 개인화 추천 업데이트 큐 
        self.channel.queue_declare(
            queue="personalization",
            durable=True
        )
    
    def create_worker_connection(self, worker_id: str) -> tuple[pika.BlockingConnection, pika.channel.Channel]:
        """워커별 독립적인 RMQ 연결 생성"""
        try:
            # 워커별 독립적인 연결 생성
            credentials = pika.PlainCredentials(
                username=settings.RMQ_USERNAME,
                password=settings.RMQ_PASSWORD
            )
            
            connection_params = pika.ConnectionParameters(
                host=settings.RMQ_HOST,
                port=settings.RMQ_PORT,
                virtual_host=settings.RMQ_VHOST,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300,
                connection_attempts=3,
                retry_delay=2
            )
            
            connection = pika.BlockingConnection(connection_params)
            channel = connection.channel()
            
            # 큐 선언
            self._declare_queues_on_channel(channel)
            
            # 워커 연결 정보 저장
            self._worker_connections[worker_id] = {
                'connection': connection,
                'channel': channel,
                'created_at': time.time()
            }
            
            logger.info(f"워커별 RMQ 연결 생성됨 (Worker ID: {worker_id})")
            return connection, channel
            
        except Exception as e:
            logger.error(f"워커별 RMQ 연결 생성 실패 (Worker ID: {worker_id}): {e}")
            raise
    
    def _declare_queues_on_channel(self, channel: pika.channel.Channel):
        """채널에 필요한 큐들 선언"""
        channel.queue_declare(queue="user_actions", durable=True)
        channel.queue_declare(queue="business_insights", durable=True)
        channel.queue_declare(queue="db_sync", durable=True)
        channel.queue_declare(queue="personalization", durable=True)
    
    def close_worker_connection(self, worker_id: str):
        """워커별 연결 해제"""
        try:
            if worker_id in self._worker_connections:
                worker_info = self._worker_connections[worker_id]
                
                # 채널 닫기
                if worker_info['channel'] and not worker_info['channel'].is_closed:
                    try:
                        worker_info['channel'].close()
                    except Exception as e:
                        logger.debug(f"워커 채널 닫기 실패 (무시): {e}")
                
                # 연결 닫기
                if worker_info['connection'] and not worker_info['connection'].is_closed:
                    try:
                        worker_info['connection'].close()
                    except Exception as e:
                        logger.debug(f"워커 연결 닫기 실패 (무시): {e}")
                
                # 연결 정보 제거
                del self._worker_connections[worker_id]
                logger.info(f"워커별 RMQ 연결 해제됨 (Worker ID: {worker_id})")
                
        except Exception as e:
            logger.warning(f"워커별 RMQ 연결 해제 중 예외 발생 (Worker ID: {worker_id}): {e}")
    
    def get_worker_connection_info(self) -> dict:
        """워커별 연결 정보 반환"""
        return {
            worker_id: {
                'is_connected': not info['connection'].is_closed,
                'created_at': info['created_at'],
                'uptime': time.time() - info['created_at']
            }
            for worker_id, info in self._worker_connections.items()
        }
    
    def _is_connection_healthy(self) -> bool:
        """연결 상태가 정상인지 확인"""
        try:
            # 기본 상태 확인
            if not self.is_connected or not self.connection or not self.channel:
                return False
            
            # 연결과 채널이 닫혔는지 확인
            if self.connection.is_closed or self.channel.is_closed:
                logger.debug("RMQ 연결 또는 채널이 닫힌 상태")
                return False
            
            # connection.is_open 속성으로 연결 상태만 확인
            return self.connection.is_open
            
        except Exception as e:
            logger.debug(f"RMQ 연결 헬스체크 실패: {e}")
            return False
    
    def publish_user_action(self, data: Dict[str, Any]) -> bool:
        """사용자 행동을 RMQ로 전송"""
        start_time = time.time()
        try:
            # 연결 상태 확인 및 재연결
            if not self._is_connection_healthy():
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
            # 연결 실패 시 상태 업데이트
            self.is_connected = False
            duration = time.time() - start_time
            track_api_call("rabbitmq", "user_actions", 500, duration)
            logger.error(f"사용자 행동 전송 실패: {e}")
            return False
    
    def publish_business_insight(self, data: Dict[str, Any]) -> bool:
        """비즈니스 인사이트 업데이트를 RMQ로 전송"""
        start_time = time.time()
        try:
            # 연결 상태 확인 및 재연결
            if not self._is_connection_healthy():
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
            
            # 메트릭 추적
            duration = time.time() - start_time
            track_api_call("rabbitmq", "business_insights", 200, duration)
            
            logger.info("비즈니스 인사이트 업데이트 전송")
            return True
            
        except Exception as e:
            # 연결 실패 시 상태 업데이트
            self.is_connected = False
            duration = time.time() - start_time
            track_api_call("rabbitmq", "business_insights", 500, duration)
            logger.error(f"비즈니스 인사이트 전송 실패: {e}")
            return False
    
    def publish_db_sync(self, data: Dict[str, Any]) -> bool:
        """DB 동기화 이벤트를 RMQ로 전송"""
        start_time = time.time()
        try:
            # 연결 상태 확인 및 재연결
            if not self._is_connection_healthy():
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
            
            # 메트릭 추적
            duration = time.time() - start_time
            track_api_call("rabbitmq", "db_sync", 200, duration)
            
            logger.debug(f"DB 동기화 이벤트 전송: {data.get('action', 'unknown')}")
            return True
            
        except Exception as e:
            # 연결 실패 시 상태 업데이트
            self.is_connected = False
            duration = time.time() - start_time
            track_api_call("rabbitmq", "db_sync", 500, duration)
            logger.error(f"DB 동기화 이벤트 전송 실패: {e}")
            return False
    
    
    def disconnect(self):
        """RMQ 연결 해제 (모든 워커 연결 포함)"""
        try:
            # 모든 워커 연결 해제
            for worker_id in list(self._worker_connections.keys()):
                self.close_worker_connection(worker_id)
            
            # 메인 채널 닫기
            if self.channel and not self.channel.is_closed:
                try:
                    self.channel.close()
                except Exception as e:
                    logger.debug(f"채널 닫기 실패 (무시): {e}")
            
            # 메인 연결 닫기
            if self.connection and not self.connection.is_closed:
                try:
                    self.connection.close()
                except Exception as e:
                    logger.debug(f"연결 닫기 실패 (무시): {e}")
            
            self.is_connected = False
            self.connection = None
            self.channel = None
            self._worker_connections.clear()
            logger.info("RMQ 연결 해제 (모든 워커 포함)")
        except Exception as e:
            logger.warning(f"RMQ 연결 해제 중 예외 발생: {e}")
            # 예외가 발생해도 상태는 초기화
            self.is_connected = False
            self.connection = None
            self.channel = None
            self._worker_connections.clear()

# 싱글톤 인스턴스
rmq_manager = RMQManager()
