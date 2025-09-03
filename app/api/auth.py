from fastapi import APIRouter, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..core.logger import logger, get_user_logger
from ..core.exceptions import CustomError
from ..models.user import User, UserCreate, UserLogin, Token
from ..services.user_service import create_user, authenticate_user, get_current_user_from_token

router = APIRouter(prefix="/auth", tags=["authentication"])
security = HTTPBearer()


@router.post(
    "/register", 
    response_model=User, 
    status_code=status.HTTP_201_CREATED,
    summary="사용자 회원가입",
    description="사용자 회원가입을 위한 API"
)
async def register(user_data: UserCreate):
    """사용자 회원가입"""
    try:
        logger.info(
            f"Registration attempt for username: {user_data.username}",
            action="register", 
            username=user_data.username
        )
        
        user = await create_user(
            user_data.username,
            user_data.password
        )
        
        # 사용자별 로거로 성공 로깅
        user_logger = get_user_logger(str(user.id))
        user_logger.info(
            "User registration successful",
            action="register_success", 
            username=user.username
        )
        
        return user
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise e


@router.post(
    "/login", 
    response_model=Token,
    summary="사용자 로그인",
    description="사용자 로그인을 위한 API"
)
async def login(login_data: UserLogin, request: Request):
    """사용자 로그인"""
    try:
        # 클라이언트 IP 추출
        client_ip = request.client.host if request.client else None
        
        logger.info(
            f"Login attempt for username: {login_data.username}",
            action="login", 
            username=login_data.username,
            client_ip=client_ip
        )
        
        # client_ip를 함께 전달
        token = await authenticate_user(
            login_data.username, 
            login_data.password, 
            client_ip
        )
        
        logger.info(
            f"User logged in successfully: {login_data.username}",
            action="login_success", 
            username=login_data.username,
            client_ip=client_ip
        )
        
        return token
        
    except CustomError as e:
        logger.warning(f"Login failed: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="로그인 처리 중 오류가 발생했습니다."
        )

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """현재 인증된 사용자 반환 (FastAPI 의존성 주입용)"""
    try:
        return await get_current_user_from_token(credentials.credentials)
    except CustomError as e:
        logger.warning(f"Authentication failed: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 처리 중 오류가 발생했습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/me", response_model=User,
    summary="현재 사용자 정보 조회",
    description="현재 사용자 정보를 조회하는 API"
)
async def get_me(current_user: User = Depends(get_current_user)):
    """현재 사용자 정보 조회"""
    return current_user



