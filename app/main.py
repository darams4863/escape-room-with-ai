import time
import uuid
from datetime import datetime
from .utils.time import now_korea_iso
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .core.logger import logger, get_trace_logger
from .core.exceptions import CustomError

from .core.config import settings
from .core.connections import connections
from .api.chat import router as chat_router
from .api.auth import router as auth_router

# FastAPI 앱 생성
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI 기반 방탈출 추천 챗봇 API"
)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 특정 도메인으로 제한
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 트레이싱 미들웨어 추가
@app.middleware("http")
async def add_logging_middleware(request: Request, call_next):
    # 트레이스 ID 생성 또는 헤더에서 가져오기
    trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
    
    # 트레이스 로거 생성
    trace_logger = get_trace_logger(trace_id)
    
    # 엔드포인트 정보 추출
    endpoint_info = None
    route_name = None
    path_params = {}
    
    # FastAPI 라우트 정보 추출
    try:
        # request.scope에서 route 정보 가져오기 (더 정확한 방법)
        if "route" in request.scope:
            route = request.scope["route"]
            if hasattr(route, 'endpoint'):
                endpoint_info = f"{route.endpoint.__module__}.{route.endpoint.__name__}"
                route_name = getattr(route, 'name', None)
            if "path_params" in request.scope:
                path_params = request.scope["path_params"]
        else:
            # fallback: 수동으로 라우트 찾기
            for route in app.routes:
                match, scope = route.matches({"type": "http", "path": request.url.path, "method": request.method})
                if match.name == "full":
                    if hasattr(route, 'endpoint'):
                        endpoint_info = f"{route.endpoint.__module__}.{route.endpoint.__name__}"
                        route_name = getattr(route, 'name', None)
                    break
    except Exception:
        # 엔드포인트 정보 추출 실패 시 기본값 유지
        pass
    
    # 요청 시작 로깅
    trace_logger.info(
        f"Request started: {request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "endpoint": endpoint_info,
            "route_name": route_name,
            "query_params": str(request.query_params) if request.query_params else None,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "content_type": request.headers.get("content-type")
        }
    )
    
    # 요청 처리 시간 측정
    start_time = time.time()
    
    # 요청 처리
    response = await call_next(request)
    
    # 처리 시간 계산
    process_time = time.time() - start_time
    
    # 응답 완료 로깅
    trace_logger.info(
        f"Request completed: {response.status_code} in {process_time:.3f}s",
        extra={
            "status_code": response.status_code,
            "process_time_seconds": round(process_time, 3),
            "endpoint": endpoint_info,
            "route_name": route_name,
            "content_length": response.headers.get("content-length"),
            "response_content_type": response.headers.get("content-type")
        }
    )
    
    # 응답에 트레이스 ID와 처리 시간 추가
    response.headers["X-Trace-ID"] = trace_id
    response.headers["X-Process-Time"] = str(round(process_time, 3))
    
    return response

# 라우터 등록
app.include_router(auth_router)
app.include_router(chat_router)


@app.on_event("startup")
async def startup_event():
    """애플리케이션 시작 시 실행"""
    logger.info("🚀 Starting Escape Room AI Chatbot...")
    try:
        await connections.connect_all()
        logger.info("✅ All database connections established")
    except Exception as e:
        logger.error(f"❌ Failed to establish connections: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """애플리케이션 종료 시 실행"""
    logger.info("🛑 Shutting down Escape Room AI Chatbot...")
    try:
        await connections.disconnect_all()
        logger.info("✅ All database connections closed")
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}")


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
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "error_code": "200001",
                "message": "관리자에게 문의해주세요."
            }
        )


@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "message": "🎯 Escape Room AI Chatbot API",
        "version": settings.app_version,
        "docs": "/docs",
        "features": [
            "🤖 AI 챗봇",
            "📝 방린이 테스트", 
            "🎪 방탈출 추천",
            "🔐 JWT 인증",
            "📊 Redis 캐시"
        ]
    }


@app.get("/health")
async def health_check():
    """전체 서비스 헬스 체크"""
    try:
        # 데이터베이스 연결 상태 확인
        health_status = await connections.health_check()
        
        return {
            "status": "healthy" if health_status["overall"] else "unhealthy",
            "service": settings.app_name,
            "version": settings.app_version,
            "timestamp": now_korea_iso(),
            "connections": {
                "postgres": "✅" if health_status["postgres"] else "❌",
                "redis": "✅" if health_status["redis"] else "❌"
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "service": settings.app_name,
            "version": settings.app_version,
            "error": str(e)
        }
