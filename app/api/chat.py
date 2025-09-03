from fastapi import APIRouter, HTTPException, Depends
from ..core.logger import logger, get_user_logger
from ..core.exceptions import CustomError
from ..core.redis_manager import redis_manager

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
        # 입력 검증
        if not request.message or not request.message.strip():
            raise CustomError("VALIDATION_ERROR", "메시지를 입력해주세요.")
        
        if len(request.message) > 500:
            raise CustomError("VALIDATION_ERROR", "메시지가 너무 깁니다. (최대 1000자)")
        
        # XSS 방지: 기본적인 HTML 태그 제거
        import re
        sanitized_message = re.sub(r'<[^>]+>', '', request.message.strip())
        
        # Rate Limiting 체크
        user_id = current_user.get("id")
        is_allowed, status = await redis_manager.rate_limit_check(user_id, limit=20, window=60)
        if not is_allowed:
            raise CustomError("RATE_LIMIT_EXCEEDED", f"요청 한도를 초과했습니다. {status.get('reset_time', 60)}초 후 다시 시도해주세요.")
        
        # 사용자별 로거 사용
        user_logger = get_user_logger(str(f"{current_user.get("id")}"))
        user_logger.info(
            f"Chat request: {sanitized_message[:50]}...",
            username=current_user.get("username"),
            action="unified_chat_request"
        )
        
        # 사용자 챗 세션 확인 및 생성 
        session_info = await get_or_create_user_session(
            current_user.get("id"),  # int로 전달
            request.session_id
        )
        if not session_info:
            raise CustomError("SESSION_CREATION_FAILED")

        # 유저 기본 선호도 조회
        user_prefs = await get_user_preferences(current_user.get("id"))

        # 통합 챗봇 처리 (선호도 파악 + 방탈출 추천)
        response = await chat_with_user(
            session_info["session_id"],
            user_prefs,
            sanitized_message,  # 검증된 메시지 사용
            current_user.get("id")   
        )
        
        # 응답에 세션 ID 추가 (클라이언트가 다음 요청에 사용)
        if response:
            response.session_id = session_info["session_id"]

        user_logger.info(
            "Unified chat response generated",
            username=current_user.get("username"),
            session_id=session_info["session_id"],
            chat_type=getattr(response, 'chat_type', 'unknown'),
            is_questionnaire_active=getattr(response, 'is_questionnaire_active', False),
            action="unified_chat_response"
        )
        
        return response

    except Exception as e:
        logger.error(f"Unified chat endpoint error: {e}", error_type="unexpected_error")
        raise HTTPException(status_code=500, detail="채팅 처리 중 오류가 발생했습니다.")


