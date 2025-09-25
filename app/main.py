from contextlib import asynccontextmanager
import os
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .api.analytics import router as analytics_router
from .api.auth import router as auth_router
from .api.chat import router as chat_router
from .core.config import settings
from .core.connections import connections
from .core.exceptions import CustomError
from .core.logger import logger
from .core.monitor import collect_system_metrics, start_prometheus_server
from .utils.time import now_korea_iso
from .workers.rmq_worker import RMQWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 생명주기 관리"""
    # 시작 시 실행
    logger.info("🚀 Starting Escape Room AI Chatbot...")
    try:
        await connections.connect_all()
        
        # RMQ Worker 실행 방식 선택
        if connections.rmq.is_connected:
            worker_mode = os.getenv("RMQ_WORKER_MODE", "separate")  # separate, integrated
            
            if worker_mode == "integrated":
                # 통합 모드: 같은 프로세스에서 실행 (개발용)
                try:
                    worker_count = int(os.getenv("RMQ_WORKER_COUNT", "1"))
                    for i in range(worker_count):
                        worker = RMQWorker(worker_id=f"worker_{i+1}")
                        worker_thread = threading.Thread(
                            target=worker.start_consuming,
                            daemon=True,
                            name=f"RMQWorker-{i+1}"
                        )
                        worker_thread.start()
                        logger.info(f"📊 RMQ Worker {i+1}/{worker_count} started (integrated mode)")
                except Exception as e:
                    logger.error(f"❌ RMQ Worker 시작 실패: {e}")
            else:
                # 분리 모드: 별도 프로세스로 실행 
                logger.info("📊 RMQ Worker는 별도 프로세스로 실행됩니다")
                logger.info("💡 실행 방법: python -m app.workers.rmq_worker")
                logger.info("💡 연결은 이미 초기화되어 워커에서 재사용됩니다")
        else:
            logger.warning("⚠️ RMQ 연결이 없어 Worker를 시작하지 않습니다")
        
        
        # Prometheus 메트릭 서버 시작 
        prometheus_thread = threading.Thread(
            target=start_prometheus_server,
            args=(8001,),  # 포트 8001 사용
            daemon=True
        )
        prometheus_thread.start()
        logger.info("📈 Prometheus metrics server started on port 8001")
        
        # ML 모델 매니저는 지연 로딩으로 필요시 자동 초기화됨
        logger.info("🤖 ML 모델 매니저 준비 완료 (지연 로딩)")
        
        # 시스템 메트릭 수집 시작 
        def collect_metrics_loop():
            while True:
                collect_system_metrics()
                time.sleep(60)  # 60초마다 수집 (CPU 부하 감소)
        
        metrics_thread = threading.Thread(target=collect_metrics_loop, daemon=True)
        metrics_thread.start()
        logger.info("📊 System metrics collection started")
        
    except Exception as e:
        logger.error(f"❌ Failed to establish connections: {e}")
        raise
    
    yield  # 애플리케이션 실행
    
    # 종료 시 실행
    logger.info("🛑 Shutting down Escape Room AI Chatbot...")
    try:
        await connections.disconnect_all()
        logger.info("✅ All database connections closed")
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}")

# FastAPI 앱 생성
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI 기반 방탈출 추천 챗봇 API",
    lifespan=lifespan,  # 생명주기 관리
    # Swagger UI 설정 - 인증 정보 유지
    swagger_ui_parameters={
        "persistAuthorization": True,  # 새로고침 시에도 인증 정보 유지
    }
)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 간단한 로깅 미들웨어
@app.middleware("http")
async def simple_logging_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    logger.info(f"{request.method} {request.url.path} - {response.status_code} ({process_time:.3f}s)")
    return response

# 라우터 등록
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(analytics_router)

# Prometheus 메트릭 엔드포인트
@app.get("/metrics")
async def metrics():
    """Prometheus 메트릭 엔드포인트"""
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)



@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """전역 예외 처리 - HTTPException, CustomError, 일반 Exception 모두 처리"""
    if isinstance(exc, HTTPException):
        # HTTPException 처리 (FastAPI 기본 예외)
        logger.error(
            f"HTTPException: {exc.detail}", 
            status_code=exc.status_code,
            path=request.url.path,
            method=request.method
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail
        )
    elif isinstance(exc, CustomError):
        # CustomError 처리 (애플리케이션 커스텀 예외)
        logger.error(
            f"CustomError: {exc.message}", 
            error_code=exc.error_code, 
            http_status=exc.http_status,
            path=request.url.path,
            method=request.method
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_dict()
        )
    else:
        # 일반 Exception 처리 (예상치 못한 에러)
        logger.error(
            f"Unexpected error: {exc}", 
            error_type=type(exc).__name__,
            path=request.url.path,
            method=request.method
        )
        # 예상치 못한 에러는 CustomError로 변환
        custom_error = CustomError("INTERNAL_SERVER_ERROR")
        return JSONResponse(
            status_code=custom_error.http_status,
            content=custom_error.to_dict()
        )


@app.get("/health")
async def health_check():
    """전체 서비스 헬스 체크"""
    try:
        # 연결 상태 확인
        health_status = await connections.health_check()
        
        return {
            "status": "healthy" if health_status["overall"] else "unhealthy",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "timestamp": now_korea_iso(),
            "connections": {
                "postgres": "✅" if health_status["postgres"] else "❌",
                "redis": "✅" if health_status["redis"] else "❌",
                "rmq": "✅" if health_status["rmq"] else "❌"
            },
            "rmq_workers": {
                "count": len(health_status["rmq_workers"]),
                "workers": health_status["rmq_workers"]
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "timestamp": now_korea_iso(),
            "error": str(e)
        }
