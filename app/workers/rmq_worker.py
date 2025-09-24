"""
RMQ Worker: RabbitMQ 큐 메시지 처리 (DB 동기화, 비즈니스 인사이트, 사용자 행동 분석)
"""
import asyncio
import json
from typing import Any, Dict, List

from ..core.logger import logger
from ..core.postgres_manager import postgres_manager
from ..core.redis_manager import redis_manager
from ..core.rmq_manager import rmq_manager
from ..repositories.analytics_repository import (
    get_popular_regions,
    get_popular_themes,
    get_user_trends,
    log_analytics_event,
)
from ..repositories.chat_repository import update_session
from ..repositories.user_repository import upsert_user_preferences
from ..utils.time import now_korea_iso


class RMQWorker:
    """RabbitMQ 큐 메시지 처리 Worker"""
    
    def __init__(self, worker_id: str = None):
        self.worker_id = worker_id or f"worker_{id(self)}"  # 고유 워커 ID
        self.connection = None
        self.channel = None
        self.is_running = False
        self.MAX_RETRIES = 5
        self.RETRY_DELAY = 5
        
        # 처리량 제어 설정
        self.batch_size = 50  # 한 번에 처리할 메시지 수
        self.max_concurrent = 10  # 동시 처리 가능한 워커 수
        self.processing_timeout = 30  # 처리 타임아웃 (초)
    
    def start_consuming(self):
        """메시지 소비 시작"""
        try:
            # 완전히 독립적인 이벤트 루프에서 실행
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._run_worker())
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"RMQ Worker 실행 실패 (ID: {self.worker_id}): {e}")
    
    async def _run_worker(self):
        """워커 실행"""
        # 워커별 연결 초기화 (공유 풀 사용)
        await self._init_worker_connections()
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # 독립적인 RMQ 연결 생성
                self._create_independent_connection()
                
                # Consumer 설정
                self._setup_consumers()
                
                logger.info(f"RMQ Worker 시작됨 (ID: {self.worker_id})")
                
                # 메시지 처리 시작 (동기)
                self._start_consuming()
                self.is_running = True
                break  # 성공하면 루프 종료
                
            except Exception as e:
                logger.warning(f"RMQ Worker 시작 시도 {attempt + 1}/{self.MAX_RETRIES} 실패 (ID: {self.worker_id}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                    self.RETRY_DELAY *= 2  # 지수 백오프
                else:
                    logger.error(f"RMQ Worker 최종 시작 실패 (ID: {self.worker_id}): {e}")
                    return
    
    
    async def _init_worker_connections(self):
        """워커별 연결 확인 (이미 초기화된 매니저 사용)"""
        try:
            # 연결 상태 확인만 수행 (초기화는 메인 앱에서 이미 완료됨)
            postgres_ok = postgres_manager.pool is not None
            redis_ok = redis_manager.pool is not None
            rmq_ok = rmq_manager.is_connected
            
            if not postgres_ok:
                logger.warning("PostgreSQL 풀이 초기화되지 않음 - 워커에서 초기화 시도")
                await postgres_manager.init(
                    min_size=2,
                    max_size=5,
                    command_timeout=30,
                    server_settings={'timezone': 'Asia/Seoul'}
                )
            
            if not redis_ok:
                logger.warning("Redis 풀이 초기화되지 않음 - 워커에서 초기화 시도")
                await redis_manager.init(
                    max_connections=10,
                    socket_timeout=30,
                    socket_connect_timeout=10,
                    retry_on_timeout=True,
                    health_check_interval=30
                )
            
            if not rmq_ok:
                logger.warning("RMQ가 연결되지 않음 - 워커에서 연결 시도")
                rmq_manager.connect()
            
            logger.info(f"워커 연결 확인 완료 (Worker ID: {self.worker_id}) - PostgreSQL: {postgres_ok}, Redis: {redis_ok}, RMQ: {rmq_ok}")
        except Exception as e:
            logger.error(f"워커 연결 확인 실패 (Worker ID: {self.worker_id}): {e}")
            raise
    
    def _create_independent_connection(self):
        """워커별 독립적인 RMQ 연결 생성 (RMQ Manager 사용)"""
        # RMQ Manager를 통해 워커별 연결 생성
        self.connection, self.channel = rmq_manager.create_worker_connection(self.worker_id)
        logger.info(f"워커별 RMQ 연결 생성됨 (Worker ID: {self.worker_id})")
    
    def _setup_consumers(self):
        """Consumer 설정"""
        # QoS 설정으로 처리량 제어
        self.channel.basic_qos(prefetch_count=self.batch_size)
        
        # 사용자 행동 처리
        self.channel.basic_consume(
            queue="user_actions",
            on_message_callback=self._process_user_action_sync,
            auto_ack=False  # 수동 ACK로 안정성 향상
        )
        
        # 비즈니스 인사이트 업데이트 처리
        self.channel.basic_consume(
            queue="business_insights",
            on_message_callback=self._process_business_insight_sync,
            auto_ack=False
        )
        
        # DB 동기화 처리
        self.channel.basic_consume(
            queue="db_sync",
            on_message_callback=self._process_db_sync_sync,
            auto_ack=False
        )
    
    def _process_user_action_sync(self, channel, method, properties, body):
        """사용자 행동 처리"""
        try:
            data = json.loads(body)
            logger.info(f"사용자 행동 메시지 처리: {data.get('action', 'unknown')}")
            
            # 동기적으로 처리 (비동기 함수 제거)
            self._handle_user_action_sync(data)
            channel.basic_ack(delivery_tag=method.delivery_tag)
                
        except Exception as e:
            logger.error(f"사용자 행동 처리 실패: {e}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    
    def _process_business_insight_sync(self, channel, method, properties, body):
        """비즈니스 인사이트 처리"""
        try:
            data = json.loads(body)
            logger.info(f"비즈니스 인사이트 메시지 처리: {data.get('days', 'unknown')}일")
            
            # 동기적으로 처리 (비동기 함수 제거)
            self._handle_business_insight_sync(data)
            channel.basic_ack(delivery_tag=method.delivery_tag)
                
        except Exception as e:
            logger.error(f"비즈니스 인사이트 처리 실패: {e}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    
    def _process_db_sync_sync(self, channel, method, properties, body):
        """DB 동기화 처리"""
        try:
            data = json.loads(body)
            logger.info(f"DB 동기화 메시지 처리: {data.get('action', 'unknown')}")
            
            self._handle_db_sync_sync(data)
            channel.basic_ack(delivery_tag=method.delivery_tag)
                
        except Exception as e:
            logger.error(f"DB 동기화 처리 실패: {e}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def _handle_user_action_sync(self, data: Dict[str, Any]):
        """사용자 행동 처리"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._handle_user_action(data))
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"사용자 행동 처리 중 오류: {e}")
    
    def _handle_business_insight_sync(self, data: Dict[str, Any]):
        """비즈니스 인사이트 처리"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._handle_business_insight(data))
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"비즈니스 인사이트 업데이트 중 오류: {e}")
    
    def _handle_db_sync_sync(self, data: Dict[str, Any]):
        """DB 동기화 처리"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._handle_db_sync(data))
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"DB 동기화 처리 중 오류: {e}")
    
    def _save_conversation_to_db_sync(self, user_id: int, session_id: str, messages: List[Dict[str, Any]]):
        """대화 기록을 DB에 저장 (동기)"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._update_chat_session_db(user_id, session_id, messages))
                logger.info(f"대화 기록 DB 저장 완료: user_id={user_id}, session_id={session_id}")
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"대화 기록 DB 저장 실패: {e}")
    
    async def _update_chat_session_db(self, user_id: int, session_id: str, messages: List[Dict[str, Any]]):
        """chat_sessions 테이블의 conversation_history 업데이트"""
        try:
            # conversation_history JSON 구성
            conversation_data = {
                "messages": messages
            }
            
            # DB 업데이트
            async with postgres_manager.get_connection() as conn:
                await conn.execute("""
                    UPDATE chat_sessions 
                    SET conversation_history = $1, updated_at = NOW()
                    WHERE session_id = $2 AND user_id = $3
                """, json.dumps(conversation_data, ensure_ascii=False), session_id, user_id)
                
                logger.info(f"chat_sessions 테이블 업데이트 완료: session_id={session_id}")
                
        except Exception as e:
            logger.error(f"chat_sessions 테이블 업데이트 실패: {e}")
            raise

    def _start_consuming(self):
        """메시지 소비 시작"""
        try:
            # 메시지 소비 시작 
            self.channel.start_consuming()
            logger.info("RMQ Worker 메시지 소비 시작됨")
            
        except Exception as e:
            logger.error(f"메시지 소비 실패: {e}")
            raise
    
    async def _handle_user_action(self, data: Dict[str, Any]):
        """사용자 행동 처리 로직"""
        try:
            # 1. 사용자 행동 저장
            action_data = data.get("data", {})
            # 참여도 점수 계산 (일일 채팅 횟수 기반)
            daily_chat_count = action_data.get("daily_chat_count", 1)
            engagement_score = min(1.0, daily_chat_count / 10.0)  # 최대 1.0, 10회 이상이면 1.0
            
            # JSONB info 데이터 구성
            info_data = {
                "message_length": action_data.get("message_length", 0),
                "response_time_ms": action_data.get("response_time_ms", 0),
                "daily_chat_count": daily_chat_count,
                "has_recommendations": action_data.get("has_recommendations", False)
            }
            
            await log_analytics_event(
                user_id=data.get("user_id"),
                session_id=data.get("session_id"),
                event_type=data.get("action"),
                region=action_data.get("region"),
                theme=action_data.get("theme"),
                engagement_score=engagement_score,
                info=info_data
            )
            
            # 3. 실시간 인사이트 업데이트 (7일간 데이터)
            await self._update_business_insights(days=7)
            
            logger.info(f"사용자 행동 처리 완료: {data.get('action')}")
            
        except Exception as e:
            logger.error(f"사용자 행동 처리 중 오류: {e}")
    
    
    async def _handle_business_insight(self, data: Dict[str, Any]):
        """비즈니스 인사이트 업데이트 처리 로직"""
        try:
            days = data.get("days", 7)
            await self._update_business_insights(days=days)
            logger.info(f"비즈니스 인사이트 업데이트 완료: {days}일")
            
        except Exception as e:
            logger.error(f"비즈니스 인사이트 업데이트 중 오류: {e}")
    
    async def _handle_db_sync(self, data: Dict[str, Any]):
        """DB 동기화 처리 로직"""
        try:
            action = data.get("action", "")
            user_id = data.get("user_id")
            session_id = data.get("session_id")
            
            # 대화 동기화 처리
            if action == "conversation_sync":
                await self._sync_conversation_to_db(user_id, session_id, data)
                return
            
            # 선호도 동기화 처리
            if action == "preference_sync":
                await self._sync_preferences_to_db(user_id, data)
                return
                
        except Exception as e:
            logger.error(f"DB 동기화 처리 중 오류: {e}")
    
    async def _sync_conversation_to_db(self, user_id: int, session_id: str, data: Dict[str, Any]):
        """대화 내용을 DB에 동기화"""
        try:
            messages = data.get("messages", [])
            if messages:
                await update_session(session_id, json.dumps({"messages": messages}, ensure_ascii=False))
                logger.info(f"Conversation synced to DB: user_id={user_id}, session_id={session_id}")
            
        except Exception as e:
            logger.error(f"Conversation sync to DB failed: {e}")
    
    async def _sync_preferences_to_db(self, user_id: int, data: Dict[str, Any]):
        """사용자 선호도를 DB에 동기화"""
        try:
            preferences = data.get("preferences", {})
            if preferences:
                await upsert_user_preferences(user_id, preferences)
                logger.info(f"Preferences synced to DB: user_id={user_id}")
            
        except Exception as e:
            logger.error(f"Preferences sync to DB failed: {e}")
    
    async def _update_business_insights(self, days: int = 7):
        """비즈니스 인사이트 업데이트"""
        try:
            # 인기 지역 조회
            popular_regions = await get_popular_regions(days=days)
            
            # 인기 테마 조회
            popular_themes = await get_popular_themes(days=days)
            
            # 사용자 트렌드 조회
            user_trends = await get_user_trends(days=days)
            
            # 인사이트 데이터 저장 (business_insights 테이블에)
            await self._save_business_insights({
                "popular_regions": [r.model_dump() for r in popular_regions],
                "popular_themes": [t.model_dump() for t in popular_themes],
                "user_trends": [t.model_dump() for t in user_trends],
                "generated_at": now_korea_iso(),
                "period_days": days
            })
            
            logger.info(f"비즈니스 인사이트 업데이트 완료: {days}일")
            
        except Exception as e:
            logger.error(f"비즈니스 인사이트 업데이트 실패: {e}")
    
    async def _save_business_insights(self, insights_data: Dict[str, Any]):
        """비즈니스 인사이트를 DB에 저장"""
        try:
            async with postgres_manager.get_connection() as conn:
                # 기존 데이터 업데이트 또는 새로 삽입
                await conn.execute("""
                    INSERT INTO business_insights (insight_type, period, data, updated_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (insight_type, period)
                    DO UPDATE SET 
                        data = EXCLUDED.data,
                        updated_at = EXCLUDED.updated_at
                """, 
                "comprehensive_insights",
                f"{insights_data['period_days']}days",
                json.dumps(insights_data, ensure_ascii=False, default=str),
                now_korea_iso()
                )
                
        except Exception as e:
            logger.error(f"비즈니스 인사이트 저장 실패: {e}")
    
    def stop(self):
        """Worker 중지 (RMQ Manager를 통한 연결 해제)"""
        try:
            self.is_running = False
            
            # 소비 중지
            if self.channel and not self.channel.is_closed:
                try:
                    self.channel.stop_consuming()
                except Exception as e:
                    logger.debug(f"소비 중지 실패 (무시): {e}")
            
            # RMQ Manager를 통해 워커 연결 해제
            rmq_manager.close_worker_connection(self.worker_id)
            self.connection = None
            self.channel = None
            logger.info(f"RMQ Worker 중지됨 (Worker ID: {self.worker_id})")
        except Exception as e:
            logger.warning(f"RMQ Worker 중지 중 예외 발생 (Worker ID: {self.worker_id}): {e}")
            # 예외가 발생해도 상태는 초기화
            self.is_running = False
            self.connection = None
            self.channel = None

# Worker 실행 함수
def run_rmq_worker():
    """RMQ Worker 실행"""
    worker = RMQWorker()
    try:
        worker.start_consuming()
    except KeyboardInterrupt:
        logger.info("Worker 중지 요청됨")
        worker.stop()
    except Exception as e:
        logger.error(f"Worker 실행 중 오류: {e}")
        worker.stop()

if __name__ == "__main__":
    run_rmq_worker()