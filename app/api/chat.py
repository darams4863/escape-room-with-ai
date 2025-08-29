from fastapi import APIRouter, HTTPException, Depends
from ..core.logger import logger, get_user_logger
from ..core.exceptions import CustomError

from ..models.escape_room import ChatRequest, ChatResponse
from ..models.user import User
from ..services.chat_service import (
    get_or_create_user_session,
    chat_with_user,
)
from ..repositories.user_repository import get_user_preferences
from ..api.auth import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/", response_model=ChatResponse)
async def unified_chat(
    request: ChatRequest, 
    current_user: User = Depends(get_current_user)
):
    """통합 AI 챗봇 - 선호도 파악 + 방탈출 추천 (인증 필요)"""
    try:
        # 사용자별 로거 사용
        user_logger = get_user_logger(str(current_user.id))
        user_logger.info(
            f"Chat request: {request.message[:50]}...",
            username=current_user.username,
            action="unified_chat_request"
        )
        
        # 사용자 ID를 request에 추가
        request.user_id = str(current_user.id)
        
        # 사용자 챗 세션 확인 및 생성 
        session_info = await get_or_create_user_session(
            current_user.id,  # int로 전달
            request.session_id
        )
        if not session_info:
            raise CustomError("SESSION_CREATION_FAILED")

        # 유저 기본 선호도 조회
        user_prefs = await get_user_preferences(current_user.id)

        session_id = session_info["session_id"]  # dict에서 session_id 추출

        # 통합 챗봇 처리 (선호도 파악 + 방탈출 추천)
        response = await chat_with_user(
            session_id,
            user_prefs,
            request.message,  
            current_user.id   
        )
        
        # 응답에 세션 ID 추가 (클라이언트가 다음 요청에 사용)
        if response:
            response.session_id = session_id

        user_logger.info(
            "Unified chat response generated",
            username=current_user.username,
            session_id=session_id,
            chat_type=getattr(response, 'chat_type', 'unknown'),
            is_questionnaire_active=getattr(response, 'is_questionnaire_active', False),
            action="unified_chat_response"
        )
        
        return response
        
    except CustomError as e:
        logger.warning(f"Unified chat error: {e.message}", error_code=e.error_code)
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"Unified chat endpoint error: {e}", error_type="unexpected_error")
        raise HTTPException(status_code=500, detail="채팅 처리 중 오류가 발생했습니다.")


@router.get("/health")
async def health_check():
    """헬스 체크 엔드포인트"""
    return {
        "status": "healthy", 
        "service": "unified-escape-room-chatbot",
        "features": [
            "preference_questionnaire",
            "escape_room_recommendation", 
            "intelligent_chat",
            "session_management"
        ]
    }