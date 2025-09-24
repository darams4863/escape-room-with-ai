from fastapi import APIRouter, Depends

from ..core.auth import get_current_user
from ..models.escape_room import ChatRequest, ChatResponse
from ..models.user import User
from ..services.chat_service import chat_with_user

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/", response_model=ChatResponse)
async def unified_chat(
    request: ChatRequest, 
    current_user: User = Depends(get_current_user)
):
    """통합 AI 챗봇 - 선호도 파악 + 방탈출 추천"""
    return await chat_with_user(
        user_id=current_user.id,
        message=request.message,
        session_id=request.session_id
    )


