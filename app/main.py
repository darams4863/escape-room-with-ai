import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .core.logger import logger
from .core.exceptions import CustomError
from .core.config import settings
from .core.connections import connections
from .api.chat import router as chat_router
from .api.auth import router as auth_router
from .utils.time import now_korea_iso


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 생명주기 관리"""
    # 시작 시 실행
    logger.info("🚀 Starting Escape Room AI Chatbot...")
    try:
        await connections.connect_all()
        logger.info("✅ All database connections established")
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
    title=settings.app_name,
    version=settings.app_version,
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
            "timestamp": now_korea_iso(),
            "error": str(e)
        }
