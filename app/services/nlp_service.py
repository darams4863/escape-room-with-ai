"""챗 메세지 의도 분석 전담 서비스 (LLM + DB Fallback)"""

import json
from typing import Dict, Any, List
from ..core.logger import logger
from ..repositories.escape_room_repository import get_intent_patterns_from_db


class Intent:
    """사용자 의도 파악"""
    QUESTION = "question" # 질문
    RECOMMENDATION = "recommendation" # 추천 요청
    GENERAL_CHAT = "general_chat" # 일반 대화
    PREFERENCE_CHECK = "preference_check" # 선호도 체크 / cf. QUESTIONNAIRE = "questionnaire"

async def analyze_intent(user_message: str) -> Dict[str, Any]:
    """하이브리드 의도 분석: LLM 우선, DB fallback"""
    try:
        # 1. LLM 기반 의도 분석 시도
        llm_result = await _analyze_intent_with_llm(user_message)
        
        # 2. LLM 결과가 신뢰할 만하면 사용
        # e.g. {'intent': 'recommendation', 'confidence': 0.85, 'entities': {'지역': '강남', '테마': '공포'}, 'reasoning': '사용자가 강남에서 남자친구와 할만한 테마를 추천해주길 원하며, 공포 테마는 절대 안된다고 명시함'}
        if llm_result.get("confidence", 0) > 0.6:
            logger.info(f"LLM intent analysis successful: {llm_result['intent']}")
            return llm_result
        
        # 3. LLM 실패 시 DB 패턴 매칭으로 fallback
        logger.info("LLM analysis failed, falling back to pattern matching")
        return await _analyze_intent_pattern_fallback(user_message)
        
    except Exception as e:
        logger.error(f"Hybrid intent analysis error: {e}")
        return await _analyze_intent_pattern_fallback(user_message)

async def _analyze_intent_with_llm(user_message: str) -> Dict[str, Any]:
    """LLM을 사용한 의도 분석"""
    try:
        # LLM 서비스의 기존 LLM 인스턴스 사용
        from .chat_service import llm_service
        llm = llm_service.llm
        
        prompt = f"""
다음 사용자 메시지의 의도를 분석해주세요.

사용자 메시지: {user_message}

의도를 다음 중에서 선택하고 JSON 형태로 응답하세요:
1. "recommendation" - 방탈출 추천 요청
2. "question" - 방탈출 관련 질문  
3. "general_chat" - 일반적인 대화
4. "preference_check" - 선호도 체크

추가로 다음 정보도 포함해주세요:
- confidence: 의도 파악 신뢰도 (0.0-1.0)
- entities: 추출된 엔티티 (preferred_region, excluded_region, preferred_themes, excluded_themes, group_size, relationship, difficulty, activity_level, duration_minutes, price_per_person, group_size_min, group_size_max, company, rating)
- reasoning: 의도 파악 근거

**중요**: 
- "공포 테마는 안돼", "호러 싫어" 같은 표현은 제외 테마(excluded_themes)로 분류
- 선호하는 테마(preferred_themes)와 제외하는 테마(excluded_themes)를 구분해서 추출
    - 예: 아래 테마 예시에서 공포는 excluded_themes에 들어가고, 나머지는 모두 preferred_themes에 들어간다
- preferred_region, excluded_region도 하단 지역 예시에서 정확하게 추출
    - 예: 사용자 요청이 "강남에서 추리 테마로 추천해줘" 일 때, preferred_region은 "강남"이고, excluded_region은 []이다

**테마와 지역 예시**:
테마: '스릴러', '기타', '판타지', '추리', '호러/공포', '잠입', '모험/탐험', '감성', '코미디', '드라마', '범죄', '미스터리', 'SF', '19금', '액션', '역사', '로맨스', '아이', '타임어택'
지역: '서울', '강남', '강동구', '강북구', '신림', '건대', '구로구', '노원구', '동대문구', '동작구', '홍대', '신촌', '성동구', '성북구', '잠실', '양천구', '영등포구', '용산구', '은평구', '대학로', '중구', '경기', '고양', '광주', '구리', '군포', '김포', '동탄', '부천', '성남', '수원', '시흥', '안산', '안양', '용인', '의정부', '이천', '일산', '평택', '하남', '화성', '인천', '남동구', '미추홀구', '부평구', '연수구', '전북', '군산', '익산', '전주', '충남', '당진', '천안', '경남', '양산', '진주', '창원', '강원', '강릉', '원주', '춘천', '제주', '서귀포시', '제주시', '충북', '청주', '전남', '목포', '순천', '여수', '경북', '경주', '구미', '영주', '포항'

**엔티티 추출 예시:**
- "강남에서 추리 테마로 추천해줘" → {{"preferred_region": ["강남"], "preferred_themes": ["추리"]}}
- "공포 테마는 절대 안돼" → {{"excluded_themes": ["공포"]}}
- "2명이 할 수 있는 거" → {{"group_size": 2}}
- "남자친구랑 할만한" → {{"relationship": "커플", "preferred_themes": ["로맨스", "드라마"]}}

JSON 응답:
"""
        
        # LangChain 방식으로 호출
        from langchain.schema import HumanMessage
        response = await llm.agenerate([[HumanMessage(content=prompt)]])
        
        # 응답 추출
        response_text = response.generations[0][0].text.strip()
        
        # JSON 파싱
        try:
            intent_data = json.loads(response_text)
            return intent_data
        except json.JSONDecodeError:
            logger.warning("LLM response JSON parsing failed")
            raise Exception("JSON parsing failed")
        
    except Exception as e:
        logger.error(f"LLM intent analysis error: {e}")
        raise e

async def _analyze_intent_pattern_fallback(user_message: str) -> Dict[str, Any]:
    """DB 기반 패턴 매칭 fallback"""
    message = user_message.lower().strip()
    
    # DB에서 의도 패턴 조회
    intent_patterns = await _get_intent_patterns()
    
    best_match = None
    best_confidence = 0.0
    
    # 각 의도별 패턴 매칭
    for intent_name, patterns in intent_patterns.items():
        for pattern_data in patterns:
            pattern = pattern_data['pattern']
            confidence = pattern_data['confidence']
            
            if pattern in message:
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = {
                        "intent": intent_name,
                        "confidence": confidence,
                        "reasoning": f"Pattern fallback: '{pattern}'",
                        "method": "pattern_matching"
                    }
    
    # 매칭된 의도가 있으면 반환
    if best_match:
        return best_match
    
    # 기본값: 일반 대화
    return {
        "intent": Intent.GENERAL_CHAT,
        "confidence": 0.3,
        "reasoning": "No pattern matched - fallback to general chat",
        "method": "fallback_default"
    }


async def _get_intent_patterns() -> Dict[str, List[Dict]]:
    """의도 패턴 조회 (로깅 + 예외 처리)"""
    try:
        patterns = await get_intent_patterns_from_db()
        logger.info(f"Intent patterns loaded successfully: {len(patterns)} intents")
        return patterns
    except Exception as e:
        logger.error(f"Failed to fetch intent patterns: {e}")
        # Fallback 데이터 반환
        fallback_patterns = {
            "recommendation": [
                {"pattern": "추천", "confidence": 1.0},
                {"pattern": "찾아", "confidence": 0.9}
            ]
        }
        logger.warning(f"Using fallback intent patterns: {fallback_patterns}")
        return fallback_patterns