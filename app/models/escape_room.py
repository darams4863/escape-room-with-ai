import pytz
from pydantic import BaseModel, Field, field_validator
from typing import List
from datetime import datetime


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
        if isinstance(v, datetime):
            # UTC 시간을 한국 시간으로 변환
            korea_tz = pytz.timezone('Asia/Seoul')
            if v.tzinfo is None:
                v = pytz.UTC.localize(v)
            return v.astimezone(korea_tz)
        return v

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S') if v else None
        }


class UserPreferenceBase(BaseModel):
    """Base user preference model"""
    experience_level: str = Field(default="beginner", description="경험 수준")
    preferred_difficulty: int | None = Field(None, ge=1, le=5, description="선호 난이도")
    preferred_activity_level: int | None = Field(None, ge=1, le=5, description="선호 활동성")
    preferred_regions: List[str] = Field(default=[], description="선호 지역")
    preferred_group_size: int | None = Field(None, ge=1, description="선호 그룹 크기")


class UserPreferenceCreate(UserPreferenceBase):
    """Create user preference model"""
    user_id: str = Field(..., description="사용자 ID")


class UserPreference(UserPreferenceBase):
    """User preference model with ID and timestamps"""
    id: int
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class ChatMessage(BaseModel):
    """Chat message model"""
    role: str = Field(..., description="메시지 역할 (user/assistant)")
    content: str = Field(..., description="메시지 내용")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ChatSession(BaseModel):
    """Chat session model"""
    session_id: str = Field(..., description="세션 ID")
    user_id: str | None = Field(None, description="사용자 ID")
    conversation_history: List[ChatMessage] = Field(default=[], description="대화 기록")
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str = Field(..., description="사용자 메시지")
    session_id: str | None = Field(None, description="세션 ID")
    user_id: str | None = Field(None, description="사용자 ID")


class ChatResponse(BaseModel):
    """통합 Chat response model - 방린이 테스트 + 방탈출 추천"""
    message: str = Field(..., description="AI 응답")
    session_id: str = Field(..., description="세션 ID")
    
    # 방린이 테스트 관련
    questionnaire: dict | None = Field(None, description="방린이 테스트 질문/응답")
    
    # 방탈출 추천 관련
    recommendations: List[EscapeRoom] | None = Field(None, description="추천 방탈출")
    user_profile: dict | None = Field(None, description="사용자 프로필 정보")
    
    # NLP 분석 결과
    intent: dict | None = Field(None, description="의도 분석 결과")
    entities: dict | None = Field(None, description="엔티티 추출 결과")
    
    # 챗봇 상태
    chat_type: str = Field(default="general", description="채팅 타입 (questionnaire/recommendation/general)")
    is_questionnaire_active: bool = Field(default=False, description="방린이 테스트 진행 중 여부")
