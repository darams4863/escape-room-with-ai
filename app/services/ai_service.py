"""AI 서비스 (NLP + RAG) - 의도 분석, 엔티티 추출, 검색"""

import json
import time
from typing import Any, Dict, List

from langchain.schema import HumanMessage

from ..core.exceptions import CustomError
from ..core.llm import llm
from ..core.logger import logger
from ..core.monitor import track_api_call, track_performance
from ..repositories.escape_room_repository import get_intent_patterns_from_db
from ..utils.time import now_korea_iso

# =============================================================================
# 의도 분석 및 엔티티 추출
# =============================================================================

@track_performance("intent_analysis")
async def analyze_intent(user_message: str) -> Dict[str, Any]:
    """하이브리드 의도 분석: LLM 우선, DB fallback"""
    try:
        # 1. LLM 기반 의도 분석 시도
        llm_result = await _analyze_intent_with_llm(user_message)
        
        # 2. LLM 결과가 신뢰할 만하면 사용
        if llm_result.get("confidence", 0) > 0.6:
            logger.info(f"LLM intent analysis successful: {llm_result}")
            return llm_result
        
        # TODO: # 🔥 여기에 품질 평가 추가 (기존 신뢰도 판단 방식 활용) e.g. evaluate_response_quality 이런거 만들어서 실제로 llm이 반환한 resposne가 유저의 요청과 부합하는지 품질을 평가하는 로직 -> 매트릭 수집 -> grafana로 실패 매트릭 보여주기 && 재시도 로직이 빔.
        # TODO:  실제 BLEU, ROUGE, BERTScore 라이브러리 도입 및 계산 도입을 고려해 볼것 .
        # 그리고 나서 위에서 스코어 기반으로 특정 점수 이하일 때 자동 재생성 하는 시스템 도입할 것 

        # 3. LLM 실패 시 DB 패턴 매칭으로 fallback
        logger.info("LLM analysis failed, falling back to pattern matching")
        return await _analyze_intent_pattern_fallback(user_message)
        
    except Exception as e:
        logger.error(f"Hybrid intent analysis error: {e}")
        return await _analyze_intent_pattern_fallback(user_message)

async def _analyze_intent_with_llm(user_message: str) -> Dict[str, Any]:
    """LLM을 사용한 응답 유형 분석"""
    try:
        prompt = f"""
사용자 메시지를 분석하여 어떤 종류의 응답을 원하는지 파악하고, 방탈출 관련 정보를 추출해주세요.

사용자 메시지: {user_message}

다음 중 하나의 응답 유형을 선택하고 JSON 형태로 응답하세요:

1. "room_recommendation" - 구체적인 방탈출 추천 요청
   - 예: "강남에서 추리 테마로 추천해줘", "4명이 할 수 있는 방탈출 찾아줘", "남자친구랑 강남에서 방탈출할건데 추천해줘"
   
2. "room_inquiry" - 방탈출에 대한 정보 질문
   - 예: "방탈출이 뭐야?", "난이도 3은 어느 정도야?", "방탈출 가격은 보통 얼마야?", "방탈출 어떻게 하는거야?"
   
3. "general_chat" - 일반적인 대화나 인사
   - 예: "안녕하세요", "오늘 날씨가 좋네요", "고마워요", "잘했어요", "좋은 하루 보내"

추가 정보:
- confidence: 응답 유형 파악 신뢰도 (0.0-1.0)
- entities: 추출된 엔티티 (모든 관련 정보 포함)
- reasoning: 응답 유형 선택 근거

**엔티티 추출 예시**:
- "강남에서 추리 테마로 추천해줘" → {{"preferred_regions": ["강남"], "preferred_themes": ["추리"]}}
- "공포 테마는 절대 안돼" → {{"excluded_themes": ["공포"]}}
- "4명이 할 수 있는 거" → {{"preferred_group_size": 4}}
- "남자친구랑 강남에서 방탈출할건데" → {{"preferred_group_size": 2, "preferred_regions": ["강남"]}}
- "가격은 20000원대" → {{"price_min": 20000, "price_max": 30000}}
- "최대 3만원까지" → {{"price_max": 30000}}
- "최소 2만원 이상" → {{"price_min": 20000}}
- "나 완전 방린이야" → {{"experience_level": "방린이"}}
- "나는 초보자야" → {{"experience_level": "방생아"}}
- "피자나 치킨 관련된 테마로 방탈출 있어?" → {{"keywords": "피자,치킨"}}

**경험 레벨 매핑**:
- "방생아", "초보자", "처음", "신입" → "방생아"
- "방린이", "조금 해봤어", "기본은 알아" → "방린이"  
- "방소년", "중급자", "어느정도 해봤어" → "방소년"
- "방어른", "고급자", "많이 해봤어" → "방어른"
- "방신", "전문가", "고인물" → "방신"
- "방장로", "최고수", "마스터" → "방장로"

**중요**: 반드시 유효한 JSON 형태로만 응답하세요. ```json```이나 다른 마크다운 형식을 사용하지 마세요.

JSON 응답:
"""
        
        # LangChain 방식으로 호출 (토큰 사용량 포함)
        start_time = time.time()
        response_text, token_usage = await llm.generate_with_messages_and_usage([HumanMessage(content=prompt)])
        response_time = (time.time() - start_time) * 1000
        
        # 실제 토큰 사용량 기반 비용 계산
        prompt_tokens = token_usage.get('prompt_tokens', 0)
        completion_tokens = token_usage.get('completion_tokens', 0)
        
        # GPT-4o-mini 가격 (2025년 9월 20일 기준)
        # cf. https://platform.openai.com/docs/pricing
        input_cost = (prompt_tokens / 1000000) * 0.15  # $0.15 per 1M tokens
        output_cost = (completion_tokens / 1000000) * 0.60  # $0.60 per 1M tokens
        total_cost = input_cost + output_cost
        
        # 한국 원화 환율 계산 (1 USD = 1500 KRW)
        total_cost_krw = total_cost * 1500
        total_tokens = prompt_tokens + completion_tokens
        logger.info(f"Intent 분석 비용: ${total_cost:.6f} (₩{total_cost_krw:.2f}) - 실제 토큰: {total_tokens} (입력: {prompt_tokens}, 출력: {completion_tokens})")
        
        # API 호출 추적
        track_api_call(
            service="openai",
            endpoint="analyze_intent", 
            status_code=200,
            duration_seconds=response_time / 1000,
            model="gpt-4o-mini",
            cost_usd=total_cost
        )
        
        # 응답 정리
        response_text = response_text.strip()
        
        # JSON 파싱
        try:
            intent_data = json.loads(response_text)
            intent_data.setdefault("timestamp", now_korea_iso())
            return intent_data
        except json.JSONDecodeError as e:
            logger.warning(f"LLM response JSON parsing failed: {e}")
            logger.warning(f"Response text: {response_text}")
            raise Exception("JSON parsing failed")
        
    except Exception as e:
        logger.error(f"LLM intent analysis error: {e}")
        # AI API 관련 에러인지 확인
        if any(keyword in str(e).lower() for keyword in ["openai", "api", "llm", "model", "gpt", "claude", "gemini"]):
            raise CustomError("AI_API_CALL_ERROR", "AI API 호출 중 오류가 발생했습니다.")
        else:
            raise CustomError("CHATBOT_ERROR", "챗봇 처리 중 오류가 발생했습니다.")

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
        "intent": "general_chat",
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

