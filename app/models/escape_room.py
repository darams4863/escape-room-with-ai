from datetime import datetime
import re
from typing import List

from pydantic import BaseModel, Field, field_validator

from ..core.exceptions import CustomError
from ..utils.time import now_korea, to_korea_time


class EscapeRoomBase(BaseModel):
    """Base escape room model"""
    name: str = Field(..., description="방탈출 이름")
    description: str = Field(..., description="방탈출 설명")
    difficulty_level: int = Field(..., ge=1, le=5, description="난이도 (1-5)")
    activity_level: int = Field(..., ge=1, le=5, description="활동성 (1-5)")
    region: str = Field(..., description="지역")
    group_size_min: int = Field(default=2, ge=1, description="최소 인원")
    group_size_max: int = Field(default=6, ge=1, description="최대 인원")
    duration_minutes: int = Field(..., description="소요 시간(분)")
    theme: str = Field(..., description="테마")
    price_per_person: int = Field(..., description="1인당 가격")


class EscapeRoomCreate(EscapeRoomBase):
    """Create escape room model"""
    pass


class EscapeRoom(EscapeRoomBase):
    """Escape room model with ID and timestamps"""
    id: int
    created_at: datetime
    updated_at: datetime
    
    @field_validator('created_at', 'updated_at', mode='before')
    @classmethod
    def format_datetime(cls, v):
        """시간을 한국 시간으로 포맷팅"""
        return to_korea_time(v) if isinstance(v, datetime) else v

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S') if v else None
        }


class ChatMessage(BaseModel):
    """Chat message model"""
    role: str = Field(..., description="메시지 역할 (user/assistant)")
    content: str = Field(..., description="메시지 내용")
    timestamp: datetime = Field(default_factory=now_korea)


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str = Field(..., min_length=1, max_length=500, description="사용자 메시지 (1-500자)")
    session_id: str | None = Field(None, description="세션 ID")
    
    @field_validator('message')
    @classmethod
    def validate_message(cls, v: str) -> str:
        """메시지 검증"""
        if not v or not v.strip():
            raise CustomError("VALIDATION_ERROR", "메시지는 비어있을 수 없습니다.")
        
        # XSS 방지: 기본적인 HTML 태그 제거
        sanitized = re.sub(r'<[^>]+>', '', v.strip())
        
        if len(sanitized) > 500:
            raise CustomError("VALIDATION_ERROR", "메시지가 너무 깁니다. (최대 500자)")
            
        return sanitized


class ChatResponse(BaseModel):
    """통합 Chat response model"""
    message: str = Field(..., description="AI 응답")
    recommendations: List[EscapeRoom] | None = Field(None, description="추천 방탈출")
    session_id: str = Field(..., description="세션 ID")
    chat_type: str | None = Field(None, description="채팅 타입")
    intent: dict | None = Field(None, description="의도 분석 결과")
    entities: dict | None = Field(None, description="엔티티 추출 결과")
    user_prefs: dict | None = Field(None, description="사용자 선호사항")
    conversation_history: List[dict] | None = Field(None, description="대화 기록")
