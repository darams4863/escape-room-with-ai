"""RAG (Retrieval-Augmented Generation) 서비스 - tsvector 기반 검색"""

from typing import List, Dict, Any
from ..core.logger import logger
from ..core.connections import postgres_manager
from ..core.llm import llm
from ..core.monitor import track_performance
from ..services.nlp_service import extract_entities_from_message


class RAGService:
    """RAG 기반 방탈출 추천 서비스"""
    
    def __init__(self):
        self.max_context_rooms = 5  # LLM에 제공할 최대 방탈출 수
        
    @track_performance("rag_retrieval")
    async def retrieve_relevant_rooms(
        self, 
        user_message: str, 
        user_prefs: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """하이브리드 검색: tsvector 우선, pgvector 폴백"""
        
        try:
            # 1. 사용자 메시지에서 엔티티 추출
            entities = await extract_entities_from_message(user_message)
            
            # 2. tsvector 기반 검색 시도 (search_vector가 있는 경우)
            relevant_rooms = await self._search_by_tsvector(user_message, entities, user_prefs)
            
            # 3. tsvector 결과가 부족하면 pgvector로 폴백
            if len(relevant_rooms) < 3:
                logger.info("tsvector results insufficient, falling back to pgvector")
                fallback_rooms = await self._search_by_pgvector(user_message, entities, user_prefs)
                
                # 두 결과를 결합 (중복 제거)
                combined_rooms = self._combine_search_results(relevant_rooms, fallback_rooms)
                return combined_rooms[:self.max_context_rooms]
            
            return relevant_rooms[:self.max_context_rooms]
            
        except Exception as e:
            logger.error(f"RAG room retrieval error: {e}")
            # 최종 폴백: pgvector만 사용
            try:
                entities = await extract_entities_from_message(user_message)
                return await self._search_by_pgvector(user_message, entities, user_prefs)
            except Exception as fallback_error:
                logger.error(f"Fallback pgvector search also failed: {fallback_error}")
                return []
    
    async def _search_by_tsvector(
        self, 
        user_message: str, 
        entities: Dict[str, Any],
        user_prefs: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """tsvector 기반 방탈출 검색"""
        
        try:
            # 검색 키워드 구성
            search_terms = self._build_search_terms(user_message, entities, user_prefs)
            
            if not search_terms:
                return []
            
            # tsvector 쿼리 구성 (OR 조건으로 키워드 검색)
            search_query = " | ".join(search_terms)
            
            # 사용자 선호도 기반 필터링 조건 추가
            where_conditions = []
            params = [search_query]
            param_idx = 2
            
            # 난이도 필터
            if user_prefs.get('experience_level'):
                difficulty_map = {'beginner': [1, 2], 'intermediate': [2, 3, 4], 'expert': [4, 5]}
                preferred_difficulty = difficulty_map.get(user_prefs['experience_level'], [1, 2, 3, 4, 5])
                where_conditions.append(f"difficulty_level = ANY(${param_idx})")
                params.append(preferred_difficulty)
                param_idx += 1
            
            # 그룹 사이즈 필터
            if user_prefs.get('group_size'):
                group_size = user_prefs['group_size']
                where_conditions.append(f"group_size_min <= ${param_idx} AND group_size_max >= ${param_idx}")
                params.append(group_size)
                param_idx += 1
            
            # 지역 필터
            if entities.get('preferred_regions'):
                where_conditions.append(f"(region = ANY(${param_idx}) OR sub_region = ANY(${param_idx}))")
                params.append(entities['preferred_regions'])
                param_idx += 1
            
            # 테마 필터
            if entities.get('preferred_themes'):
                where_conditions.append(f"theme = ANY(${param_idx})")
                params.append(entities['preferred_themes'])
                param_idx += 1
            
            # WHERE 절 구성
            where_clause = ""
            if where_conditions:
                where_clause = f"AND {' AND '.join(where_conditions)}"
            
            # search_vector 컬럼 존재 여부 확인
            try:
                query = f"""
                    SELECT 
                        id, name, description, theme, region, sub_region,
                        difficulty_level, activity_level, group_size_min, group_size_max,
                        duration_minutes, price_per_person, company, rating,
                        image_url, source_url, booking_url,
                        ts_rank(search_vector, plainto_tsquery('korean', $1)) as rank
                    FROM escape_rooms
                    WHERE search_vector @@ plainto_tsquery('korean', $1)
                    {where_clause}
                    ORDER BY rank DESC, rating DESC NULLS LAST
                    LIMIT 15
                """
            except Exception as column_error:
                if "search_vector" in str(column_error):
                    logger.warning("search_vector column not found, falling back to pgvector")
                    return []
                raise
            
            async with postgres_manager.get_connection() as conn:
                rows = await conn.fetch(query, *params)
            
            results = []
            for row in rows:
                results.append({
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description", ""),
                    "theme": row.get("theme"),
                    "region": row.get("region"),
                    "sub_region": row.get("sub_region"),
                    "difficulty_level": row.get("difficulty_level"),
                    "activity_level": row.get("activity_level"),
                    "group_size_min": row.get("group_size_min"),
                    "group_size_max": row.get("group_size_max"),
                    "duration_minutes": row.get("duration_minutes"),
                    "price_per_person": row.get("price_per_person"),
                    "company": row.get("company"),
                    "rating": float(row["rating"]) if row.get("rating") is not None else None,
                    "image_url": row.get("image_url"),
                    "source_url": row.get("source_url"),
                    "booking_url": row.get("booking_url"),
                    "similarity": float(row.get("rank", 0.0))
                })
            
            return results
            
        except Exception as e:
            logger.error(f"tsvector search error: {e}")
            return []
    
    def _build_search_terms(
        self, 
        user_message: str, 
        entities: Dict[str, Any],
        user_prefs: Dict[str, Any]
    ) -> List[str]:
        """검색 키워드 구성"""
        
        search_terms = []
        
        # 1. 엔티티에서 추출된 키워드
        if entities.get('preferred_regions'):
            search_terms.extend(entities['preferred_regions'])
        
        if entities.get('preferred_themes'):
            search_terms.extend(entities['preferred_themes'])
        
        # 2. 메시지에서 직접 추출된 키워드
        message_keywords = self._extract_keywords_from_message(user_message)
        search_terms.extend(message_keywords)
        
        # 3. 사용자 선호도 기반 키워드
        if user_prefs.get('preferred_themes'):
            search_terms.extend(user_prefs['preferred_themes'])
        
        if user_prefs.get('preferred_regions'):
            search_terms.extend(user_prefs['preferred_regions'])
        
        # 4. 중복 제거 및 빈 문자열 제거
        search_terms = list(set([term.strip() for term in search_terms if term.strip()]))
        
        return search_terms
    
    def _extract_keywords_from_message(self, message: str) -> List[str]:
        """메시지에서 검색 키워드 추출"""
        
        keywords = []
        
        # 방탈출 관련 키워드
        escape_keywords = [
            '방탈출', '테마', '방', '룸', '미션', '퍼즐', '추리', '스릴러', 
            '호러', '판타지', '잠입', '모험', '감성', '코미디', 'SF', '액션'
        ]
        for keyword in escape_keywords:
            if keyword in message:
                keywords.append(keyword)
        
        # 지역 키워드
        region_keywords = [
            '서울', '강남', '홍대', '신촌', '건대', '부산', '대구', '인천', 
            '경기', '수원', '성남', '고양', '부천', '안양', '의정부'
        ]
        for keyword in region_keywords:
            if keyword in message:
                keywords.append(keyword)
        
        # 난이도/경험 키워드
        difficulty_keywords = ['쉬운', '어려운', '초보', '전문', '고급', '중급']
        for keyword in difficulty_keywords:
            if keyword in message:
                keywords.append(keyword)
        
        return keywords
    
    async def _search_by_pgvector(
        self, 
        user_message: str, 
        entities: Dict[str, Any],
        user_prefs: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """pgvector 기반 의미적 검색 (폴백용)"""
        try:
            from ..repositories.escape_room_repository import get_embedding_based_recommendations
            
            # 사용자 메시지 임베딩 생성
            query_embedding = await llm.create_embedding(user_message)
            
            # 벡터 기반 검색으로 관련 방탈출 조회
            semantic_rooms = await get_embedding_based_recommendations(
                query_embedding=query_embedding,
                user_level=user_prefs.get('experience_level', 1),
                preferred_region=entities.get('preferred_regions', []),
                excluded_region=entities.get('excluded_regions', []),
                preferred_themes=entities.get('preferred_themes', []),
                excluded_themes=entities.get('excluded_themes', []),
                difficulty=entities.get('difficulty', []),
                activity_level=user_prefs.get('activity_level', 1),
                duration_minutes=entities.get('duration', None),
                price_per_person=entities.get('price', None),
                group_size_min=user_prefs.get('group_size', 2),
                group_size_max=user_prefs.get('group_size', 6),
                company=entities.get('company', None),
                rating=entities.get('rating', None)
            )
            
            # 검색 타입 추가
            for room in semantic_rooms:
                room["search_type"] = "semantic"
            
            return semantic_rooms
            
        except Exception as e:
            logger.error(f"pgvector search error: {e}")
            return []
    
    def _combine_search_results(
        self, 
        tsvector_rooms: List[Dict[str, Any]], 
        pgvector_rooms: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """tsvector와 pgvector 검색 결과 결합"""
        
        # 중복 제거를 위한 ID 기반 딕셔너리
        combined = {}
        
        # tsvector 결과 (가중치 0.7 - 우선순위)
        for room in tsvector_rooms:
            room_id = room["id"]
            room["similarity"] = room["similarity"] * 0.7
            room["search_type"] = "keyword"
            combined[room_id] = room
        
        # pgvector 결과 (가중치 0.3 - 폴백)
        for room in pgvector_rooms:
            room_id = room["id"]
            if room_id in combined:
                # 이미 있는 경우 가중 평균
                combined[room_id]["similarity"] = (
                    combined[room_id]["similarity"] + room["similarity"] * 0.3
                )
                combined[room_id]["search_type"] = "hybrid"
            else:
                room["similarity"] = room["similarity"] * 0.3
                room["search_type"] = "semantic"
                combined[room_id] = room
        
        # 유사도 기준으로 정렬
        sorted_rooms = sorted(combined.values(), key=lambda x: x["similarity"], reverse=True)
        
        return sorted_rooms
    
    def format_rooms_for_llm(self, rooms: List[Dict[str, Any]]) -> str:
        """방탈출 정보를 LLM이 이해하기 쉬운 형태로 포맷팅"""
        
        if not rooms:
            return "관련 방탈출 정보를 찾을 수 없습니다."
        
        formatted_rooms = []
        for i, room in enumerate(rooms, 1):
            room_info = f"""
{i}. **{room['name']}** ({room['company']})
   - 지역: {room['region']} {room.get('sub_region', '')}
   - 테마: {room['theme']}
   - 난이도: {room['difficulty_level']}/5
   - 인원: {room['group_size_min']}~{room['group_size_max']}명
   - 시간: {room['duration_minutes']}분
   - 가격: {room['price_per_person']:,}원/인
   - 평점: {room['rating']}/5.0
   - 설명: {room.get('description', '상세 설명 없음')}
   - 유사도: {room['similarity']:.2f}
"""
            formatted_rooms.append(room_info)
        
        return "\n".join(formatted_rooms)
    
    async def generate_rag_response(
        self, 
        user_message: str, 
        user_prefs: Dict[str, Any],
        conversation_history: List[Dict[str, str]]
    ) -> str:
        """RAG 기반 응답 생성"""
        
        try:
            # 1. 관련 방탈출 정보 검색
            relevant_rooms = await self.retrieve_relevant_rooms(user_message, user_prefs)
            
            # 2. 검색된 정보를 LLM용으로 포맷팅
            rooms_context = self.format_rooms_for_llm(relevant_rooms)
            
            # 3. RAG 프롬프트 구성
            rag_prompt = f"""
당신은 방탈출 전문 추천 챗봇입니다. 아래 제공된 방탈출 정보를 바탕으로 사용자의 요청에 정확하고 구체적으로 답변해주세요.

**사용자 선호도:**
- 경험 수준: {user_prefs.get('experience_level', '방생아')}
- 활동성: {user_prefs.get('activity_level', '보통')}
- 그룹 크기: {user_prefs.get('group_size', 4)}명
- 선호 지역: {user_prefs.get('preferred_regions', [])}
- 선호 테마: {user_prefs.get('preferred_themes', [])}

**관련 방탈출 정보:**
{rooms_context}

**사용자 요청:** {user_message}

**응답 가이드라인:**
1. 제공된 방탈출 정보를 바탕으로 구체적인 추천을 해주세요
2. 방탈출 이름, 위치, 가격, 평점 등 구체적인 정보를 포함해주세요
3. 사용자의 선호도와 요청사항을 고려한 맞춤형 추천을 해주세요
4. 친근하고 도움이 되는 톤으로 답변해주세요
5. 관련 정보가 부족한 경우 솔직하게 말씀해주세요

응답:"""
            
            # 4. LLM으로 응답 생성
            response = await llm.generate_response(
                conversation_history=conversation_history,
                user_level=user_prefs.get('experience_level', '방생아'),
                user_prefs=user_prefs,
                custom_prompt=rag_prompt
            )
            
            return response
            
        except Exception as e:
            logger.error(f"RAG response generation error: {e}")
            return "죄송합니다. 방탈출 정보를 가져오는 중 오류가 발생했습니다. 다시 시도해주세요."
    
    async def should_use_rag(self, user_message: str, user_prefs: Dict[str, Any]) -> bool:
        """RAG 사용 여부 결정"""
        
        # 1. 선호도가 완성된 사용자인지 확인
        required_prefs = ['experience_level', 'activity_level', 'group_size']
        if not all(key in user_prefs for key in required_prefs):
            return False
        
        # 2. 추천 관련 키워드가 있는지 확인
        recommendation_keywords = [
            '추천', '추천해', '추천해줘', '어디가', '어디서', '어떤', '좋은', 
            '방탈출', '테마', '지역', '가격', '난이도', '평점'
        ]
        
        message_lower = user_message.lower()
        has_recommendation_intent = any(keyword in message_lower for keyword in recommendation_keywords)
        
        return has_recommendation_intent


# RAG 서비스 인스턴스
rag_service = RAGService()
