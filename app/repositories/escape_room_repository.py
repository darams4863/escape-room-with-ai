"""방탈출 관련 Repository"""

from typing import Any, Dict, List

from ..core.connections import postgres_manager
from ..core.exceptions import CustomError
from ..core.llm import llm
from ..core.logger import logger


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

async def get_hybrid_recommendations(
    user_message: str,
    user_prefs: Dict[str, Any],
    keywords: str = ""
) -> List[Dict[str, Any]]:
    """하이브리드 검색: tsvector 우선, pgvector 보조"""
    try:
        # 1단계: tsvector로 빠른 검색 (LLM 키워드 추출 포함)
        tsvector_results = await search_with_tsvector(
            user_prefs, 
            user_message, 
            keywords
        )
        
        # 2단계: 결과가 10개 이상이면 tsvector만 사용
        if len(tsvector_results) >= 10:
            logger.info(f"tsvector 검색으로 충분한 결과 ({len(tsvector_results)}개)")
            return tsvector_results[:10]
        
        # 3단계: 결과가 부족하면 pgvector로 보완
        logger.info(f"tsvector 결과 부족 ({len(tsvector_results)}개), pgvector로 보완")
        
        # LLM으로 임베딩 생성 (이때만 비용 발생)
        query_embedding = await llm.create_embedding(user_message)
        logger.info(f"생성된 임베딩 차원: {len(query_embedding)}")
        
        # pgvector로 추가 검색
        vector_results = await search_with_pgvector(query_embedding, user_prefs)
        
        # 4단계: 결과 결합 및 중복 제거
        combined_results = combine_search_results(tsvector_results, vector_results)
        
        return combined_results[:10]
        
    except Exception as e:
        logger.error(f"하이브리드 검색 오류: {e}")
        return []


async def search_with_tsvector(
    user_prefs: Dict[str, Any],
    user_message: str = "",
    keywords: str = ""
) -> List[Dict[str, Any]]:
    """tsvector 기반 검색 + 키워드 LIKE 검색"""
    try:
        # 1. 키워드 추출 (사용자 메시지에서 직접)
        if not keywords and user_message:
            # 간단한 키워드 추출 (LLM 없이)
            keywords = user_message.strip()
        
        # 2. user_prefs에서 검색 키워드 구성
        search_terms = []
        
        # 추출된 키워드 추가
        if keywords:
            search_terms.append(keywords)
        
        # 리스트 타입 필드들 (빈 리스트 제외)
        for field in ['preferred_regions', 'preferred_themes', 'excluded_themes']:
            value = user_prefs.get(field)
            if value and isinstance(value, list) and len(value) > 0:
                search_terms.extend(value)
        
        # 단일 값 필드들 (None, 빈 문자열, 0 제외)
        for field in ['experience_level', 'preferred_difficulty', 'preferred_activity_level', 'preferred_group_size']:
            value = user_prefs.get(field)
            if value is not None and value != "" and value != 0:
                search_terms.append(str(value))
        
        # 숫자 필드들 (None, 0 제외)
        for field in ['experience_count', 'price_min', 'price_max']:
            value = user_prefs.get(field)
            if value is not None and value != 0:
                search_terms.append(str(value))
        
        search_query = " ".join(search_terms)
        
        if not search_query.strip():
            raise CustomError("ROOM_NOT_FOUND", "검색 조건이 없습니다.")
        
        # 3. 사용자 선호도 기반 WHERE 조건
        where_conditions = []
        params = [search_query]
        param_idx = 2  # $1은 검색어
        
        # 키워드가 있으면 LIKE 검색 조건 추가
        if keywords:
            # 사용자 메시지를 직접 검색어로 사용
            where_conditions.append(f"(name ILIKE ${param_idx} OR description ILIKE ${param_idx})")
            params.append(f"%{keywords}%")
            param_idx += 1
        
        # 지역 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('preferred_regions'):
        #     where_conditions.append(f"(region = ANY(${param_idx}) OR sub_region = ANY(${param_idx}))")
        #     params.append(user_prefs['preferred_regions'])
        #     param_idx += 1
        
        # 테마 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('preferred_themes'):
        #     where_conditions.append(f"theme = ANY(${param_idx})")
        #     params.append(user_prefs['preferred_themes'])
        #     param_idx += 1
        
        # 제외 테마 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('excluded_themes'):
        #     where_conditions.append(f"NOT (theme = ANY(${param_idx}))")
        #     params.append(user_prefs['excluded_themes'])
        #     param_idx += 1
        
        # 인원수 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('preferred_group_size'):
        #     group_size = user_prefs['preferred_group_size']
        #     where_conditions.append(f"group_size_min <= ${param_idx} AND group_size_max >= ${param_idx}")
        #     params.append(group_size)
        #     param_idx += 1
        
        # 난이도 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('preferred_difficulty'):
        #     difficulty = user_prefs['preferred_difficulty']
        #     where_conditions.append(f"difficulty_level = ${param_idx}")
        #     params.append(difficulty)
        #     param_idx += 1
        
        # 활동성 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('preferred_activity_level'):
        #     activity_level = user_prefs['preferred_activity_level']
        #     where_conditions.append(f"activity_level = ${param_idx}")
        #     params.append(activity_level)
        #     param_idx += 1
        
        # 가격 필터 (사용자 선호도 - 주석처리)
        # if user_prefs.get('price_min'):
        #     price_min = user_prefs['price_min']
        #     where_conditions.append(f"price_per_person >= ${param_idx}")
        #     params.append(price_min)
        #     param_idx += 1
        
        # if user_prefs.get('price_max'):
        #     price_max = user_prefs['price_max']
        #     where_conditions.append(f"price_per_person <= ${param_idx}")
        #     params.append(price_max)
        #     param_idx += 1
        
        where_clause = f"WHERE {' OR '.join(where_conditions)}" if where_conditions else ""
        
        query = f"""
            SELECT 
                id, name, description, theme, region, sub_region,
                difficulty_level, activity_level, group_size_min, group_size_max,
                duration_minutes, price_per_person, company, rating,
                image_url, source_url, booking_url,
                ts_rank(to_tsvector('simple', name || ' ' || description || ' ' || theme), 
                        plainto_tsquery('simple', $1)) AS rank
            FROM escape_rooms 
            {where_clause}
            OR to_tsvector('simple', name || ' ' || description || ' ' || theme) 
                @@ plainto_tsquery('simple', $1)
            ORDER BY rank DESC
            LIMIT 15
        """
        
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
            
    except Exception as e:
        logger.error(f"tsvector 검색 오류: {e}")
        return []


async def search_with_pgvector(
    query_embedding: List[float], 
    user_prefs: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """pgvector 기반 검색 (이미 생성된 임베딩 사용)"""
    try:
        # Python 리스트를 PostgreSQL vector 리터럴로 변환
        vector_literal = '[' + ','.join(map(str, query_embedding)) + ']'
        
        # 사용자 선호도 기반 WHERE 조건
        where_conditions = []
        params = [vector_literal]  # vector 리터럴 문자열로 전달
        param_idx = 2  # $1은 임베딩
        
        # 지역 필터 (사용자 선호도)
        if user_prefs.get('preferred_regions'):
            where_conditions.append(f"(region = ANY(${param_idx}) OR sub_region = ANY(${param_idx}))")
            params.append(user_prefs['preferred_regions'])
            param_idx += 1
        
        # 테마 필터 (사용자 선호도)
        if user_prefs.get('preferred_themes'):
            where_conditions.append(f"theme = ANY(${param_idx})")
            params.append(user_prefs['preferred_themes'])
            param_idx += 1
        
        # 제외 테마 필터 (사용자 선호도)
        if user_prefs.get('excluded_themes'):
            where_conditions.append(f"NOT (theme = ANY(${param_idx}))")
            params.append(user_prefs['excluded_themes'])
            param_idx += 1
        
        # 인원수 필터 (사용자 선호도)
        if user_prefs.get('preferred_group_size'):
            group_size = user_prefs['preferred_group_size']
            where_conditions.append(f"group_size_min <= ${param_idx} AND group_size_max >= ${param_idx}")
            params.append(group_size)
            param_idx += 1
        
        # 난이도 필터 (사용자 선호도)
        if user_prefs.get('preferred_difficulty'):
            difficulty = user_prefs['preferred_difficulty']
            where_conditions.append(f"difficulty_level = ${param_idx}")
            params.append(difficulty)
            param_idx += 1
        
        # 활동성 필터 (사용자 선호도)
        if user_prefs.get('preferred_activity_level'):
            activity_level = user_prefs['preferred_activity_level']
            where_conditions.append(f"activity_level = ${param_idx}")
            params.append(activity_level)
            param_idx += 1
        
        # 가격 필터 (사용자 선호도)
        if user_prefs.get('price_min'):
            price_min = user_prefs['price_min']
            where_conditions.append(f"price_per_person >= ${param_idx}")
            params.append(price_min)
            param_idx += 1
        
        if user_prefs.get('price_max'):
            price_max = user_prefs['price_max']
            where_conditions.append(f"price_per_person <= ${param_idx}")
            params.append(price_max)
            param_idx += 1
        
        where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
        
        query = f"""
            SELECT 
                id, name, description, theme, region, sub_region,
                difficulty_level, activity_level, group_size_min, group_size_max,
                duration_minutes, price_per_person, company, rating,
                image_url, source_url, booking_url,
                1 - (embedding <=> $1::vector) AS similarity
            FROM escape_rooms 
            {where_clause}
            ORDER BY similarity DESC
            LIMIT 10
        """
        
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
            
    except Exception as e:
        logger.error(f"pgvector 검색 오류: {e}")
        return []


def combine_search_results(tsvector_results: List[Dict], vector_results: List[Dict]) -> List[Dict]:
    """tsvector와 pgvector 결과 결합 및 중복 제거"""
    # ID 기반 중복 제거
    combined = {}
    
    # tsvector 결과 우선 (더 정확한 키워드 매칭)
    for room in tsvector_results:
        room_id = room['id']
        room['search_type'] = 'tsvector'
        combined[room_id] = room
    
    # pgvector 결과 추가 (의미적 유사도)
    for room in vector_results:
        room_id = room['id']
        if room_id not in combined:
            room['search_type'] = 'pgvector'
            combined[room_id] = room
    
    # tsvector 결과 우선시 정렬
    def get_score(room):
        if room.get('search_type') == 'tsvector':
            # tsvector는 높은 우선순위 (1000 + rank)
            return 1000 + room.get('rank', 0)
        else:  # pgvector
            # pgvector는 낮은 우선순위 (similarity만)
            return room.get('similarity', 0)
    
    sorted_rooms = sorted(combined.values(), key=get_score, reverse=True)
    return sorted_rooms



