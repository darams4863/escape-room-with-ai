"""방탈출 관련 Repository"""

from typing import List, Dict, Any
from ..core.logger import logger
from ..core.connections import postgres_manager, redis_manager

# nlp_service.py에서 사용
async def get_intent_patterns_from_db() -> Dict[str, List[Dict]]:
    """DB에서 의도 패턴 조회"""
    async with postgres_manager.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT 
                id,
                intent_name, 
                pattern_text, 
                confidence_weight 
            FROM intent_patterns 
            WHERE is_active = TRUE 
            ORDER BY intent_name, confidence_weight DESC"""
        )
        
        patterns = {}
        for row in rows:
            intent = row['intent_name']
            if intent not in patterns:
                patterns[intent] = []
            
            patterns[intent].append({
                'pattern': row['pattern_text'],
                'confidence': float(row['confidence_weight'])
            })
        
        return patterns

# recommendation_service.py에서 사용
async def get_embedding_based_recommendations(
    query_embedding: List[float],
    user_level: int,
    preferred_region: List[str],
    excluded_region: List[str],
    preferred_themes: List[str],
    excluded_themes: List[str],
    # preferred_group_size: int,
    # relationship: List[str],
    difficulty: List[int],
    activity_level: int,
    duration_minutes: int,
    price_per_person: int,
    group_size_min: int,
    group_size_max: int,
    company: str,
    rating: float,
) -> List[Dict[str, Any]]:
    """임베딩 기반 방탈출 추천 (pgvector + asyncpg 자리표시자 사용)"""
    try:
        where_clauses: list[str] = []
        params: list[Any] = []
        idx = 2  # $1은 임베딩

        # 지역 포함/제외
        if preferred_region:
            where_clauses.append(f"(region = ANY(${idx}) OR sub_region = ANY(${idx}))")
            params.append(preferred_region)
            idx += 1
        if excluded_region:
            where_clauses.append(f"NOT (region = ANY(${idx}) OR sub_region = ANY(${idx}))")
            params.append(excluded_region)
            idx += 1

        # 테마 포함/제외
        if preferred_themes:
            where_clauses.append(f"theme = ANY(${idx})")
            params.append(preferred_themes)
            idx += 1
        if excluded_themes:
            where_clauses.append(f"NOT (theme = ANY(${idx}))")
            params.append(excluded_themes)
            idx += 1

        # 인원 범위(방의 범위가 사용자의 범위를 커버)
        if group_size_min and group_size_max:
            where_clauses.append(f"group_size_min <= ${idx} AND group_size_max >= ${idx + 1}")
            params.extend([group_size_min, group_size_max])
            idx += 2

        # 시간/가격 상한
        if duration_minutes:
            where_clauses.append(f"duration_minutes <= ${idx}")
            params.append(duration_minutes)
            idx += 1
        if price_per_person:
            where_clauses.append(f"price_per_person <= ${idx}")
            params.append(price_per_person)
            idx += 1

        # 난이도(리스트 있으면 ANY, 없으면 user_level로 대체)
        effective_difficulty = difficulty if difficulty else [user_level]
        if effective_difficulty:
            where_clauses.append(f"difficulty_level = ANY(${idx})")
            params.append(effective_difficulty)
            idx += 1

        # 활동성
        if activity_level:
            where_clauses.append(f"activity_level = ${idx}")
            params.append(activity_level)
            idx += 1

        # 업체
        if company:
            where_clauses.append(f"company = ${idx}")
            params.append(company)
            idx += 1

        # 평점 하한
        if rating:
            where_clauses.append(f"rating >= ${idx}")
            params.append(rating)
            idx += 1

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT
                id,
                name,
                description,
                theme,
                region,
                sub_region,
                difficulty_level,
                activity_level,
                group_size_min,
                group_size_max,
                duration_minutes,
                price_per_person,
                company,
                rating,
                image_url,
                source_url,
                booking_url,
                1 - (embedding <=> $1::vector) AS similarity
            FROM escape_rooms
            {where_sql}
            ORDER BY similarity DESC, rating DESC NULLS LAST
            LIMIT ${idx}
        """

        args = [query_embedding] + params + [10]  # limit 고정(필요 시 서비스에서 인자 추가)
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(query, *args)

        results: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            results.append({
                "id": d["id"],
                "name": d["name"],
                "description": d.get("description", ""),
                "theme": d.get("theme"),
                "region": d.get("region"),
                "sub_region": d.get("sub_region"),
                "difficulty_level": d.get("difficulty_level"),
                "activity_level": d.get("activity_level"),
                "group_size_min": d.get("group_size_min"),
                "group_size_max": d.get("group_size_max"),
                "duration_minutes": d.get("duration_minutes"),
                "price_per_person": d.get("price_per_person"),
                "company": d.get("company"),
                "rating": float(d["rating"]) if d.get("rating") is not None else None,
                "image_url": d.get("image_url"),
                "source_url": d.get("source_url"),
                "booking_url": d.get("booking_url"),
                "similarity": float(d.get("similarity", 0.0)),
            })
        return results

    except Exception as e:
        logger.error(f"Embedding-based recommendation query error: {e}")
        return []

