"""방린이 테스트 질문 및 응답 모델"""

import pytz
from enum import Enum
from typing import List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


class QuestionType(str, Enum):
    """질문 타입"""
    YES_NO = "yes_no"
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    NUMBER_INPUT = "number_input"
    TEXT_INPUT = "text_input"


class QuestionnaireStep(str, Enum):
    """방린이 테스트 단계"""
    GREETING = "greeting"
    EXPERIENCE = "experience"
    FREQUENCY = "frequency"
    REGIONS = "regions"
    GROUP_SIZE = "group_size"
    LIKED_THEMES = "liked_themes"
    DISLIKED_THEMES = "disliked_themes"
    DURATION = "duration"
    ANALYSIS = "analysis"
    RECOMMENDATIONS = "recommendations"
    COMPLETED = "completed"


class QuestionOption(BaseModel):
    """질문 선택지"""
    value: str = Field(..., description="선택지 값")
    label: str = Field(..., description="선택지 표시명")
    emoji: str | None = Field(None, description="선택지 이모지")


class Question(BaseModel):
    """질문 모델"""
    step: QuestionnaireStep = Field(..., description="질문 단계")
    question_type: QuestionType = Field(..., description="질문 타입")
    title: str = Field(..., description="질문 제목")
    description: str | None = Field(None, description="질문 설명")
    options: List[QuestionOption] | None = Field(None, description="선택지 목록")
    placeholder: str | None = Field(None, description="입력 플레이스홀더")
    is_required: bool = Field(True, description="필수 응답 여부")


class UserAnswer(BaseModel):
    """사용자 응답"""
    step: QuestionnaireStep = Field(..., description="응답 단계")
    answer: str | List[str] | int = Field(..., description="응답 값")
    answered_at: datetime = Field(default_factory=datetime.utcnow, description="응답 시간")
    
    @field_validator('answered_at', mode='before')
    @classmethod
    def format_datetime(cls, v):
        """시간을 한국 시간으로 포맷팅"""
        if isinstance(v, datetime):
            korea_tz = pytz.timezone('Asia/Seoul')
            if v.tzinfo is None:
                v = pytz.UTC.localize(v)
            return v.astimezone(korea_tz)
        return v

class QuestionnaireSession(BaseModel):
    """질문 세션"""
    session_id: str = Field(..., description="세션 ID")
    user_id: int = Field(..., description="사용자 ID")
    current_step: QuestionnaireStep = Field(default=QuestionnaireStep.GREETING, description="현재 단계")
    answers: List[UserAnswer] = Field(default_factory=list, description="응답 목록")
    is_completed: bool = Field(False, description="완료 여부")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="생성 시간")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="수정 시간")
    
    @field_validator('created_at', 'updated_at', mode='before')
    @classmethod
    def format_datetime(cls, v):
        """시간을 한국 시간으로 포맷팅"""
        if isinstance(v, datetime):
            korea_tz = pytz.timezone('Asia/Seoul')
            if v.tzinfo is None:
                v = pytz.UTC.localize(v)
            return v.astimezone(korea_tz)
        return v

class QuestionnaireResponse(BaseModel):
    """질문 응답 모델"""
    session_id: str = Field(..., description="세션 ID")
    current_step: QuestionnaireStep = Field(..., description="현재 단계")
    question: Question | None = Field(None, description="다음 질문")
    message: str = Field(..., description="봇 메시지")
    is_completed: bool = Field(False, description="완료 여부")
    analysis_result: Dict[str, Any] | None = Field(None, description="분석 결과")


class AnswerRequest(BaseModel):
    """응답 요청 모델"""
    session_id: str = Field(..., description="세션 ID")
    step: QuestionnaireStep = Field(..., description="응답 단계")
    answer: str | List[str] | int = Field(..., description="응답 값")


class UserProfile(BaseModel):
    """사용자 프로필 (분석 결과)"""
    user_id: int = Field(..., description="사용자 ID")
    experience_level: str = Field(..., description="경험 수준")
    total_experience_count: int = Field(0, description="총 경험 횟수")
    preferred_regions: List[str] = Field(default_factory=list, description="선호 지역")
    preferred_group_size: int = Field(3, description="선호 그룹 사이즈")
    liked_themes: List[str] = Field(default_factory=list, description="좋아하는 테마")
    disliked_themes: List[str] = Field(default_factory=list, description="싫어하는 테마")
    preferred_duration: str = Field("60분 이하", description="선호 소요시간")
    personality_type: str = Field(..., description="성격 유형")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="생성 시간")
    
    @field_validator('created_at', mode='before')
    @classmethod
    def format_datetime(cls, v):
        """시간을 한국 시간으로 포맷팅"""
        if isinstance(v, datetime):
            korea_tz = pytz.timezone('Asia/Seoul')
            if v.tzinfo is None:
                v = pytz.UTC.localize(v)
            return v.astimezone(korea_tz)
        return v