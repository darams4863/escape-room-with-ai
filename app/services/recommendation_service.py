"""방탈출 추천 전담 서비스 (함수 기반)"""

from typing import Dict, Any, List
from ..models.escape_room import EscapeRoom
from ..core.logger import logger
from ..core.monitor import track_performance, track_database_operation
from ..core.redis_manager import redis_manager
from .nlp_service import analyze_intent
from ..repositories.escape_room_repository import get_embedding_based_recommendations
from ..core.llm import llm

@track_performance("recommendation_generation")
async def get_escape_room_recommendations(
    user_message: str, 
    user_prefs: Dict[str, Any]
) -> List[EscapeRoom]:
    """사용자 메세지에 따른 추천 방탈출 목록 반환 (캐싱 적용)"""
    try:
        # 1. 캐시 키 생성
        cache_key = redis_manager.generate_recommendation_cache_key(user_message, user_prefs)
        
        # 2. 캐시에서 먼저 조회
        cached_recommendations = await redis_manager.get_cached_recommendations(cache_key)
        if cached_recommendations:
            logger.info(f"Cache hit for recommendations: {cache_key}")
            return [EscapeRoom(**rec) for rec in cached_recommendations]
        
        # 3. 캐시 미스 시 새로 계산
        logger.info(f"Cache miss for recommendations: {cache_key}")
        
        # NLP 분석으로 의도와 엔티티 추출 (LLM 기반)
        intent_analysis = await analyze_intent(user_message)

        # 사용자 메시지를 임베딩
        query_embedding = await llm.create_embedding(user_message)
        
        # 개인화된 추천 조회
        rows = await get_personalized_recommendations(
            query_embedding,
            intent_analysis,
            user_prefs,
        )
        
        if not rows:
            logger.info("No personalized recommendations found")
            return []
        
        # EscapeRoom 객체로 변환
        recommendations = []
        for row in rows:
            recommendations.append(EscapeRoom(
                id=row['id'],
                name=row['name'],
                description=row['description'] or "",
                difficulty_level=row['difficulty_level'],
                activity_level=row['activity_level'],
                region=row['region'],
                sub_region=row['sub_region'],
                theme=row['theme'],
                duration_minutes=row['duration_minutes'],
                price_per_person=row['price_per_person'],
                group_size_min=row['group_size_min'],
                group_size_max=row['group_size_max'],
                company=row['company'],
                rating=row['rating'],
                created_at=row.get('created_at'),
                updated_at=row.get('updated_at')
            ))
        
        logger.info(
            f"""
                Found {len(recommendations)} personalized recommendations:
                user_prefs={user_prefs}
                intent_analysis={intent_analysis}
            """
        )
        
        # 4. 결과를 캐시에 저장
        if recommendations:
            recommendations_data = [rec.dict() for rec in recommendations]
            await redis_manager.cache_recommendations(cache_key, recommendations_data)
        
        return recommendations
        
    except Exception as e:
        logger.error(f"Personalized recommendation error: {e}")
        return []

async def get_personalized_recommendations(
    query_embedding: List[float],
    intent_analysis: Dict[str, Any],
    user_prefs: Dict[str, Any]
):
    entities = intent_analysis.get("entities", {})
    user_level = user_prefs.get("experience_level", 2)
    preferred_region = entities.get("preferred_region", user_prefs.get("preferred_region", []))
    excluded_region = entities.get("excluded_region", [])
    preferred_themes = entities.get("preferred_themes", user_prefs.get("preferred_themes", []))
    excluded_themes = entities.get("excluded_themes", [])
    # preferred_group_size = entities.get("preferred_group_size", user_prefs.get("preferred_group_size", 2))
    # relationship = entities.get("relationship", [])
    difficulty = entities.get("difficulty", user_prefs.get("difficulty", []))
    activity_level = entities.get("activity_level", user_prefs.get("activity_level", 2))
    duration_minutes = entities.get("duration_minutes", user_prefs.get("duration_minutes", 60))
    price_per_person = entities.get("price_per_person", None)
    group_size_min = entities.get("group_size_min", user_prefs.get("group_size_min", 2))
    group_size_max = entities.get("group_size_max", user_prefs.get("group_size_max", 4))
    company = entities.get("company", None)
    rating = entities.get("rating", None)

    rows = await get_embedding_based_recommendations(
        query_embedding,
        user_level,
        preferred_region,
        excluded_region,
        preferred_themes,
        excluded_themes,
        # preferred_group_size,
        # relationship,
        difficulty,
        activity_level,
        duration_minutes,
        price_per_person,
        group_size_min,
        group_size_max,
        company,
        rating,
    )
    return rows


