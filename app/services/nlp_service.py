"""챗 메세지 의도 분석 전담 서비스 (LLM + DB Fallback)"""

import json
import re
from ..utils.time import now_korea_iso
from typing import Dict, Any, List
from ..core.logger import logger
from ..repositories.escape_room_repository import get_intent_patterns_from_db
from ..core.llm import llm
from langchain.schema import HumanMessage
from ..core.config import settings


# 프롬프트 버전별 분기 함수
def _build_prompt_v1_2(user_message: str) -> str:
    """기본 프롬프트 v1.2"""
    return f"""
[VERSION INFO]
prompt_version: intent.v1.2
schema_version: entities.v1.2

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

**엔티티 추출 예시**:
- "강남에서 추리 테마로 추천해줘" → {{"preferred_region": ["강남"], "preferred_themes": ["추리"]}}
- "공포 테마는 절대 안돼" → {{"excluded_themes": ["공포"]}}
- "2명이 할 수 있는 거" → {{"group_size": 2}}
- "남자친구랑 할만한" → {{"relationship": "커플", "preferred_themes": ["로맨스", "드라마"]}}

**응답 형식**:
JSON 최상위에 "_meta" 객체를 포함하여 버전 정보를 추가하세요.

JSON 응답:
"""

def _build_prompt_v1_3(user_message: str) -> str:
    """개선된 프롬프트 v1.3 (더 구체적인 예시)"""
    return f"""
[VERSION INFO]
prompt_version: intent.v1.3
schema_version: entities.v1.3

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
- "공포 테마는 안돼", "호러 싫어", "절대 안돼" 같은 표현은 제외 테마(excluded_themes)로 분류
- 선호하는 테마(preferred_themes)와 제외하는 테마(excluded_themes)를 구분해서 추출
- 지역은 정확하게 추출

**테마와 지역 예시**:
테마: '스릴러', '기타', '판타지', '추리', '호러/공포', '잠입', '모험/탐험', '감성', '코미디', '드라마', '범죄', '미스터리', 'SF', '19금', '액션', '역사', '로맨스', '아이', '타임어택'
지역: '서울', '강남', '강동구', '강북구', '신림', '건대', '구로구', '노원구', '동대문구', '동작구', '홍대', '신촌', '성동구', '성북구', '잠실', '양천구', '영등포구', '용산구', '은평구', '대학로', '중구', '경기', '고양', '광주', '구리', '군포', '김포', '동탄', '부천', '성남', '수원', '시흥', '안산', '안양', '용인', '의정부', '이천', '일산', '평택', '하남', '화성', '인천', '남동구', '미추홀구', '부평구', '연수구', '전북', '군산', '익산', '전주', '충남', '당진', '천안', '경남', '양산', '진주', '창원', '강원', '강릉', '원주', '춘천', '제주', '서귀포시', '제주시', '충북', '청주', '전남', '목포', '순천', '여수', '경북', '경주', '구미', '영주', '포항'

**엔티티 추출 예시**:
- "강남에서 추리 테마로 추천해줘" → {{"preferred_region": ["강남"], "preferred_themes": ["추리"]}}
- "공포 테마는 절대 안돼" → {{"excluded_themes": ["호러/공포"]}}
- "호러 싫어, 스릴러도 싫어" → {{"excluded_themes": ["호러/공포", "스릴러"]}}
- "2명이 할 수 있는 거" → {{"group_size": 2}}
- "남자친구랑 할만한" → {{"relationship": "커플", "preferred_themes": ["로맨스", "드라마"]}}
- "강남에서 남자친구랑 할만한 테마 추천해줘. 공포 테마는 절대 안돼" → {{"preferred_region": ["강남"], "relationship": "커플", "preferred_themes": ["로맨스"], "excluded_themes": ["호러/공포"]}}

**응답 형식**:
JSON 최상위에 "_meta" 객체를 포함하여 버전 정보를 추가하세요.

JSON 응답:
"""

def _build_prompt_by_version(version: str, user_message: str) -> str:
    """프롬프트 버전별 분기"""
    if version == "intent.v1.3":
        return _build_prompt_v1_3(user_message)
    else:
        return _build_prompt_v1_2(user_message)  # fallback


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
        
        prompt = _build_prompt_by_version(
            settings.nlp_prompt_version, 
            user_message
        )
        
        # LangChain 방식으로 호출
        response = await llm.llm.agenerate([[HumanMessage(content=prompt)]])
        
        # 응답 추출
        response_text = response.generations[0][0].text.strip()
        
        # JSON 파싱
        try:
            intent_data = json.loads(response_text)
            
            # 메타데이터 주입 (LLM이 포함하지 않은 경우 대비)
            if "_meta" not in intent_data:
                intent_data["_meta"] = {}
            
            intent_data["_meta"].setdefault("prompt_version", settings.nlp_prompt_version)
            intent_data["_meta"].setdefault("schema_version", settings.nlp_schema_version)
            intent_data["_meta"].setdefault("timestamp", now_korea_iso())
            
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

# =============================================================================
# 선호도 답변 분석 함수들 (LLM 기반 자연어 처리)
# =============================================================================

# PreferenceAnalyzer 클래스 제거됨 - core/llm.py의 llm 인스턴스 사용

async def analyze_experience_answer(user_answer: str) -> str:
    """실무 최적화: 패턴 매칭 우선, LLM 최소 사용"""
    # 1단계: 강력한 패턴 매칭 (95% 케이스 커버)
    experienced_patterns = [
        "해봤", "경험", "갔었", "해본", "예", "네", "있어요", "있습니다", 
        "몇 번", "여러 번", "자주", "가봤", "해봤어", "갔어", "해봤습니다"
    ]
    beginner_patterns = [
        "처음", "안해봤", "몰라", "아니요", "아니", "없어요", "없습니다",
        "한 번도", "전혀", "모름", "모르겠", "안 갔", "안 해봤"
    ]
    
    user_lower = user_answer.lower()
    
    # 경험 있음 패턴 확인
    for pattern in experienced_patterns:
        if pattern in user_lower:
            return "experienced"
    
    # 경험 없음 패턴 확인
    for pattern in beginner_patterns:
        if pattern in user_lower:
            return "beginner"
    
    # 2단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        방탈출 경험 질문 답변: "{user_answer}"
        
        experienced 또는 beginner 중 하나만 답변하세요.
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip().lower()
        
        return result if result in ["experienced", "beginner"] else "beginner"
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return "beginner"  # 안전한 기본값

async def analyze_experience_count(user_answer: str) -> Dict[str, Any]:
    """실무 최적화: 숫자 추출 우선, LLM 최소 사용"""
    # 1단계: 숫자 직접 추출 
    numbers = re.findall(r'\d+', user_answer)
    
    if numbers:
        count = int(numbers[0])
        if 1 <= count <= 10:
            return {"count": count, "level": "방린이"}
        elif 11 <= count <= 30:
            return {"count": count, "level": "방소년"}
        elif 31 <= count <= 50:
            return {"count": count, "level": "방어른"}
        elif 51 <= count <= 80:
            return {"count": count, "level": "방신"}
        elif 81 <= count <= 100:
            return {"count": count, "level": "방장로"}
        elif count > 100:
            return {"count": count, "level": "방장로"}
    
    # 2단계: 키워드 패턴 매칭
    user_lower = user_answer.lower()
    if any(word in user_lower for word in ["많이", "자주", "100회", "백회"]):
        return {"count": 120, "level": "방장로"}
    elif any(word in user_lower for word in ["조금", "몇 번", "적게"]):
        return {"count": 5, "level": "방린이"}
    
    # 3단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        경험 횟수 답변: "{user_answer}"
        
        1-10, 11-30, 31-50, 51-80, 81-100, 100+ 중 하나만 답변하세요.
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip()
        
        if result == "1-10":
            return {"count": 5, "level": "방린이"}
        elif result == "11-30":
            return {"count": 20, "level": "방소년"}
        elif result == "31-50":
            return {"count": 40, "level": "방어른"}
        elif result == "51-80":
            return {"count": 65, "level": "방신"}
        elif result == "81-100":
            return {"count": 90, "level": "방장로"}
        elif result == "100+":
            return {"count": 120, "level": "방장로"}
        else:
            return {"count": 5, "level": "방린이"}  # 안전한 기본값
            
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return {"count": 5, "level": "방린이"}

async def analyze_difficulty_answer(user_answer: str) -> int:
    """실무 최적화: 이모지/키워드 우선, LLM 최소 사용"""
    # 1단계: 이모지 개수로 난이도 결정 (80% 케이스)
    difficulty = user_answer.count("🔒")
    if difficulty > 0:
        return min(difficulty, 3)  # 최대 3
    
    # 2단계: 키워드 패턴 매칭
    user_lower = user_answer.lower()
    if any(word in user_lower for word in ["쉬운", "쉽게", "초보", "1"]):
        return 1
    elif any(word in user_lower for word in ["어려운", "어렵게", "고수", "3"]):
        return 3
    elif any(word in user_lower for word in ["보통", "적당", "2"]):
        return 2
    
    # 3단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        난이도 답변: "{user_answer}"
        
        1, 2, 3 중 하나만 답변하세요.
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip()
        
        return int(result) if result in ["1", "2", "3"] else 2
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return 2  # 안전한 기본값

async def analyze_activity_answer(user_answer: str) -> int:
    """실무 최적화: 키워드 우선, LLM 최소 사용"""
    # 1단계: 키워드 패턴 매칭 (95% 케이스)
    user_lower = user_answer.lower()
    if any(word in user_lower for word in ["거의 없음", "적음", "조금", "1"]):
        return 1
    elif any(word in user_lower for word in ["많음", "활발", "많이", "3"]):
        return 3
    elif any(word in user_lower for word in ["보통", "적당", "2"]):
        return 2
    
    # 2단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        활동성 답변: "{user_answer}"
        
        1, 2, 3 중 하나만 답변하세요.
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip()
        
        return int(result) if result in ["1", "2", "3"] else 2
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return 2  # 안전한 기본값

async def analyze_group_size_answer(user_answer: str) -> int:
    """실무 최적화: 숫자 추출 우선, LLM 최소 사용"""
    # 1단계: 숫자 직접 추출 (95% 케이스)
    numbers = re.findall(r'\d+', user_answer)
    
        if numbers:
        size = int(numbers[0])
        if 2 <= size <= 10:  # 합리적인 범위
            return size
        elif size == 1:
            return 2  # 1명은 2명으로 조정
        elif size > 10:
            return 10  # 10명 이상은 10명으로 제한
    
    # 2단계: 키워드 패턴 매칭
    user_lower = user_answer.lower()
    if any(word in user_lower for word in ["둘이", "2명", "두 명"]):
        return 2
    elif any(word in user_lower for word in ["셋이", "3명", "세 명"]):
        return 3
    elif any(word in user_lower for word in ["넷이", "4명", "네 명"]):
        return 4
    
    # 3단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        인원수 답변: "{user_answer}"
        
        숫자만 답변하세요 (예: 3).
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip()
        
        size = int(result) if result.isdigit() else 3
        return min(max(size, 2), 10)  # 2-10 범위로 제한
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return 3  # 안전한 기본값

async def analyze_region_answer(user_answer: str) -> str:
    """실무 최적화: 키워드 우선, LLM 최소 사용"""
    # 1단계: 키워드 패턴 매칭 (95% 케이스)
    regions = ["강남", "홍대", "건대", "신촌", "강북", "잠실", "송파", "마포", "용산"]
    for region in regions:
        if region in user_answer:
            return region
    
    # 2단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        지역 답변: "{user_answer}"
        
        강남, 홍대, 건대, 신촌, 강북, 잠실, 송파, 마포, 용산, 기타 중 하나만 답변하세요.
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip()
        
        return result if result in regions + ["기타"] else "강남"
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return "강남"  # 안전한 기본값

async def analyze_theme_answer(user_answer: str) -> str:
    """실무 최적화: 키워드 우선, LLM 최소 사용"""
    # 1단계: 키워드 패턴 매칭 (95% 케이스)
    themes = ["추리", "공포", "판타지", "SF", "스릴러", "모험", "로맨스", "코미디"]
    for theme in themes:
        if theme in user_answer:
            return theme
    
    # 2단계: 불분명한 경우만 LLM 사용 (5% 케이스)
    try:
        prompt = f"""
        테마 답변: "{user_answer}"
        
        추리, 공포, 판타지, SF, 스릴러, 모험, 로맨스, 코미디 중 하나만 답변하세요.
        """
        
        response = await llm.llm.ainvoke(prompt)
        result = response.content.strip()
        
        return result if result in themes else "추리"
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return "추리"  # 안전한 기본값