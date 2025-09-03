"""시간 관련 유틸리티 함수들"""

import pytz
from datetime import datetime

# 한국 시간대 상수
KOREA_TZ = pytz.timezone('Asia/Seoul')

def now_korea() -> datetime:
    """현재 한국 시간 반환"""
    return datetime.now(KOREA_TZ)

def now_korea_iso() -> str:
    """현재 한국 시간을 ISO 형식으로 반환"""
    return now_korea().isoformat()

def to_korea_time(dt: datetime | None) -> datetime | None:
    """UTC 시간을 한국 시간으로 변환"""
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # timezone 정보가 없으면 UTC로 가정
        dt = pytz.UTC.localize(dt)
    
    return dt.astimezone(KOREA_TZ)

def format_korea_time(dt: datetime | None) -> str:
    """한국 시간을 문자열로 포맷팅"""
    if dt is None:
        return now_korea_iso()
    
    korea_dt = to_korea_time(dt)
    return korea_dt.isoformat()

def korea_time_field():
    """Pydantic Field용 한국 시간 기본값 팩토리"""
    return lambda: now_korea()
