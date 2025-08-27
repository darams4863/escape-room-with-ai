from fastapi import APIRouter, HTTPException, Depends
from ..core.logger import logger, get_user_logger
from ..core.exceptions import CustomError

from ..models.escape_room import ChatRequest, ChatResponse
from ..models.user import User
from ..services.chatbot import chatbot
from ..services.nlp_service import nlp_service, Intent
from ..api.auth import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/", response_model=ChatResponse)
async def unified_chat(
    request: ChatRequest, 
    current_user: User = Depends(get_current_user)
):
    """통합 AI 챗봇 - 경험 등급 파악 + 방탈출 추천 (인증 필요)"""
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
        
        # 1. 경험 등급 응답 처리 (질문 진행 중인 경우)
        if request.message and any(word in request.message.lower() for word in ["네", "아니요", "해봤", "없어", "처음"]):
            experience_response = await chatbot._handle_experience_response(request.session_id, request)
            if experience_response:
                return experience_response
        
        # 2. 경험 횟수 응답 처리
        if request.message and any(char.isdigit() for char in request.message):
            count_response = await chatbot._handle_experience_count(request.session_id, request)
            if count_response:
                return count_response
        
        # 3. 일반 챗봇 대화 처리
        response = await chatbot.chat(request)
        
        # 4. NLP 분석 결과를 응답에 포함
        if hasattr(response, '__dict__'):
            response.__dict__['intent'] = {"intent": "chat", "confidence": 1.0}
            response.__dict__['entities'] = {"entities": {}}
        
        user_logger.info(
            "Unified chat response generated",
            username=current_user.username,
            session_id=response.session_id,
            chat_type=response.chat_type,
            has_recommendations=response.recommendations is not None,
            action="unified_chat_response"
        )
        
        return response
        
    except CustomError as e:
        logger.warning(f"Unified chat error: {e.message}", error_code=e.error_code)
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"Unified chat endpoint error: {e}", error_type="unexpected_error")
        raise HTTPException(status_code=500, detail="채팅 처리 중 오류가 발생했습니다.")


@router.get("/sessions/{user_id}")
async def get_user_chat_sessions(
    user_id: int,
    current_user: User = Depends(get_current_user)
):
    """사용자의 채팅 세션 목록 조회"""
    try:
        # 본인의 세션만 조회 가능
        if current_user.id != user_id:
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
        
        sessions = await chatbot.get_user_sessions(user_id)
        
        logger.info(
            f"User sessions retrieved: {len(sessions)}",
            user_id=user_id,
            username=current_user.username
        )
        
        return {
            "user_id": user_id,
            "sessions": sessions,
            "total_count": len(sessions)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user sessions error: {e}")
        raise HTTPException(status_code=500, detail="세션 조회 중 오류가 발생했습니다.")


@router.get("/health")
async def health_check():
    """헬스 체크 엔드포인트"""
    return {
        "status": "healthy", 
        "service": "unified-escape-room-chatbot",
        "features": [
            "questionnaire",
            "recommendation", 
            "nlp_analysis",
            "session_management"
        ]
    }
