from datetime import datetime

from pydantic import BaseModel, Field, field_validator, validator

from ..utils.time import to_korea_time


class UserBase(BaseModel):
    """Base user model"""
    username: str = Field(..., min_length=4, max_length=10, description="사용자명, 아이디")
    
    @validator('username')
    def validate_username(cls, v):
        if not v.isalnum():
            raise ValueError('사용자 아이디는 영문자와 숫자만 허용됩니다.')
        return v.lower()


class UserCreate(UserBase):
    """Create user model"""
    username: str = Field(..., min_length=4, max_length=10, description="사용자명, 아이디")
    password: str = Field(..., min_length=6, max_length=20, description="비밀번호")
    
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('비밀번호는 최소 6자 이상이어야 합니다.')
        return v


class UserLogin(BaseModel):
    """Login model"""
    username: str = Field(..., description="사용자명, 아이디")
    password: str = Field(..., description="비밀번호")


class User(UserBase):
    """User model with ID and timestamps"""
    id: int
    created_at: datetime
    updated_at: datetime
    is_active: bool = True
    last_login_ip: str | None = None
    last_login_at: datetime | None = None
    
    @field_validator('created_at', 'updated_at', 'last_login_at', mode='before')
    @classmethod
    def format_datetime(cls, v):
        """시간을 한국 시간으로 포맷팅"""
        return to_korea_time(v) if isinstance(v, datetime) else v

    # cf. DB 결과를 Pydantic 모델로 쉽게 변환해주는 설정 
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S') if v else None
        }

class Token(BaseModel):
    """JWT Token model"""
    access_token: str = Field(..., description="JWT 액세스 토큰")
    token_type: str = Field(default="bearer", description="토큰 타입")
    expires_in: int = Field(..., description="토큰 만료 시간(초)")



