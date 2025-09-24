"""비즈니스 인사이트용 데이터 모델"""

from datetime import datetime
from typing import Any, Dict

from pydantic import BaseModel, field_validator

from ..utils.time import to_korea_time


class BusinessInsight(BaseModel):
    """비즈니스 인사이트"""
    insight_type: str  # popular_regions, popular_themes, user_trends
    period: str  # daily, weekly, monthly
    data: Dict[str, Any]
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


class PopularRegion(BaseModel):
    """인기 지역"""
    region: str
    mention_count: int
    percentage: float
    trend: str  # up, down, stable


class PopularTheme(BaseModel):
    """인기 테마"""
    theme: str
    mention_count: int
    percentage: float
    trend: str  # up, down, stable


class UserTrend(BaseModel):
    """사용자 트렌드"""
    metric: str  # avg_session_length, preference_completion_rate, rag_usage_rate
    value: float
    period: str
    trend: str  # up, down, stable


