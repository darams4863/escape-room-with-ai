"""비즈니스 인사이트 분석 서비스 (간단 버전)"""

from typing import Dict, Any, List
from ..core.logger import logger
from ..core.monitor import track_error
from ..repositories.analytics_repository import (
    save_user_behavior, 
    get_popular_regions, 
    get_popular_themes, 
    get_user_trends
)
from ..models.analytics import UserBehavior
from ..utils.time import now_korea_iso


async def log_user_action(
    user_id: int, 
    session_id: str, 
    action: str, 
    data: Dict[str, Any]
) -> bool:
    """사용자 행동 로깅 (간단 버전)"""
    try:
        behavior = UserBehavior(
            user_id=user_id,
            session_id=session_id,
            action=action,
            timestamp=now_korea_iso(),
            data=data
        )
        
        # 동기적으로 저장 (RMQ 없이)
        success = await save_user_behavior(behavior)
        
        if success:
            logger.info(f"User action logged: {action} for user {user_id}")
        
        return success
        
    except Exception as e:
        track_error("analytics_logging_error" "/analytics/log" "POST" user_id)
        logger.error(f"Failed to log user action: {e}")
        return False


async def get_business_insights() -> Dict[str, Any]:
    """비즈니스 인사이트 조회 (간단 버전)"""
    try:
        # DB 연결 초기화
        from ..core.connections import postgres_manager
        await postgres_manager.init()
        
        # 인기 지역
        popular_regions = await get_popular_regions(days=7)
        
        # 인기 테마
        popular_themes = await get_popular_themes(days=7)
        
        # 사용자 트렌드
        user_trends = await get_user_trends(days=7)
        
        # 메모리 사용량은 connections.py에서 자동 수집됨
        
        return {
            "popular_regions": [region.dict() for region in popular_regions],
            "popular_themes": [theme.dict() for theme in popular_themes],
            "user_trends": [trend.dict() for trend in user_trends],
            "generated_at": now_korea_iso()
        }
        
    except Exception as e:
        track_error("analytics_insights_error" "/analytics/insights" "GET" None)
        logger.error(f"Failed to get business insights: {e}")
        return {
            "popular_regions": [],
            "popular_themes": [],
            "user_trends": [],
            "generated_at": now_korea_iso()
        }


async def extract_entities_from_message(message: str) -> Dict[str, Any]:
    """메시지에서 엔티티 추출"""
    entities = {
        "regions": [],
        "sub_regions": [],
        "themes": [],
        "message_length": len(message)
    }
    
    # 간단한 키워드 매칭
    regions = {
        "서울": ["중구", "강동구", "은평구", "영등포구", "잠실", "성동구", "구로구", "용산구", "대학로", "동작구", "홍대", "노원구", "건대", "신림", "양천구", "강북구", "동대문구", "성북구", "신촌", "강남"],
        "경기": ["화성", "시흥", "부천", "의정부", "구리", "군포", "동탄", "광주", "수원", "성남", "고양", "안양", "이천", "안산", "용인", "평택", "하남", "일산", "김포"],
        "강원": ["강릉", "원주", "춘천"],
        "경남": ["양산", "진주", "창원"],
        "경북": ["경주", "구미", "영주", "포항"],
        "광주": ["광산구", "서구", "북구", "동구"],
        "대구": ["달서구", "중구", "수성구"],
        "대전": ["서구", "중구", "유성구"],
        "부산": ["기장군", "중구", "사하구", "남구", "해운대구", "수영구", "금정구", "북구", "부산진구"],
        "울산": ["남구", "중구"],
        "인천": ["부평구", "미추홀구", "연수구", "남동구"],
        "전남": ["목포", "순천", "여수"],
        "전북": ["익산", "군산", "전주"],
        "제주": ["제주시", "서귀포시"],
        "충남": ["당진", "천안"],
        "충북": ["청주"]
     }

    themes = ["역사" "드라마" "모험/탐험" "로맨스" "SF" "기타" "타임어택" "추리" "감성" "스릴러" "범죄" "코미디" "판타지" "액션" "잠입" "야외" "19금" "미스터리"]
    
    for region in regions.keys():
        if region in message:
            entities["regions"].append(region)
            for sub_region in regions[region]:
                if sub_region in message:
                    entities["sub_regions"].append(sub_region)
    
    for theme in themes:
        if theme in message:
            entities["themes"].append(theme)
    
    return entities

    
