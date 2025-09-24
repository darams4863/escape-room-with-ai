from fastapi import APIRouter, HTTPException, Request, status

from ..core.exceptions import CustomError
from ..core.logger import get_user_logger, logger
from ..models.user import Token, User, UserCreate, UserLogin
from ..services.user_service import authenticate_user, create_user

router = APIRouter(prefix="/auth", tags=["Auth"])

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
    except CustomError as e:
        logger.error(f"Registration error: {e.message}")
        raise e.to_http_exception()
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
        logger.error(f"Login error: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="로그인 처리 중 오류가 발생했습니다."
        )




