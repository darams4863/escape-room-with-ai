"""방탈출 챗봇 대화 전담 서비스 (함수 기반)"""

import json
import time
from typing import Any, Dict, List
import uuid

from fastapi import HTTPException
from langchain_core.messages import HumanMessage

from ..core.connections import redis_manager, rmq
from ..core.constants import EXPERIENCE_LEVELS
from ..core.exceptions import CustomError
from ..core.llm import llm
from ..core.logger import logger
from ..core.monitor import track_chat_message, track_error, track_performance
from ..models.escape_room import ChatMessage, ChatResponse, EscapeRoom
from ..repositories.chat_repository import create_session
from ..repositories.escape_room_repository import get_hybrid_recommendations
from ..repositories.user_repository import get_user_preferences, upsert_user_preferences
from ..utils.time import now_korea_iso
from .ai_service import analyze_intent


# ===== RMQ 이벤트 전송 함수들 =====
async def _publish_conversation_sync_event(user_id: int, session_id: str, messages_data: List[Dict]):
    """대화 동기화 이벤트를 RMQ로 전송"""
    try:
        event_data = {
            "user_id": user_id,
            "session_id": session_id,
            "action": "conversation_sync",
            "data": {
                "messages": messages_data,
                "sync_type": "conversation_update",
                "timestamp": now_korea_iso()
            }
        }
        
        rmq.publish_db_sync(event_data)
        logger.debug(f"Conversation sync event published: user_id={user_id}, session_id={session_id}")
        
    except Exception as e:
        logger.error(f"Failed to publish conversation sync event: {e}")


# ===== 헬퍼 함수들 =====
def get_experience_level(count: int) -> str:
    """경험 횟수로 등급 반환"""
    for level, info in EXPERIENCE_LEVELS.items():
        if info["min_count"] <= count <= info["max_count"]:
            return level
    return list(EXPERIENCE_LEVELS.keys())[0]  # 기본값

async def _increment_daily_chat_count(user_id: int) -> int:
    """일일 채팅 횟수 증가"""
    try:
        daily_key = f"daily_chat_count:{user_id}:{now_korea_iso()[:10]}"
        
        # 키가 존재하지 않으면 새로 생성 
        if not await redis_manager.exists(daily_key):
            await redis_manager.set(daily_key, "1", ex=86400)  # 24시간 TTL
            return 1
        else:
            # 키가 존재하면 증가 
            count = await redis_manager.incr(daily_key)
            return count
    except Exception as e:
        logger.error(f"Failed to increment daily chat count: {e}")
        return 1

async def _save_conversation(user_id: int, conversation_history: List[ChatMessage]):
    """대화 저장 (Redis 즉시 + DB 배치 처리)"""
    messages_data = []
    for msg in conversation_history:
        timestamp = msg.timestamp.isoformat() if msg.timestamp and hasattr(msg.timestamp, 'isoformat') else now_korea_iso()
        messages_data.append({
            "role": msg.role,
            "content": msg.content,
            "timestamp": timestamp
        })
    
    # 1. Redis에 즉시 저장 
    user_session_key = f"user_session:{user_id}"
    existing_session = await redis_manager.get(user_session_key)
    
    if existing_session:
        session_data = json.loads(existing_session)
        session_data["messages"] = messages_data
        session_data["last_activity"] = now_korea_iso()
        session_data["needs_db_sync"] = True  # DB 동기화 필요 플래그
        
    await redis_manager.set(
        key=user_session_key,
        value=json.dumps(session_data, ensure_ascii=False),
        ex=86400
    )
    
    # 2. RMQ로 DB 동기화 이벤트 전송 
    await _publish_conversation_sync_event(user_id, session_data["session_id"], messages_data)

async def _load_conversation(user_id: int) -> List[ChatMessage]:
    """대화 로드"""
    # 통합 세션에서 메시지 로드
    user_session_key = f"user_session:{user_id}"
    existing_session = await redis_manager.get(user_session_key)
    
    if existing_session:
        session_data = json.loads(existing_session)
        messages = session_data.get("messages", [])
        return _parse_messages(messages)
    
    return []

def _parse_messages(data: Any) -> List[ChatMessage]:
    """JSON 데이터를 ChatMessage 리스트로 파싱"""
    if isinstance(data, list):
        messages_data = data
    elif isinstance(data, dict) and "messages" in data:
        messages_data = data["messages"]
    else:
        return []
    
    messages = []
    for msg_data in messages_data:
        messages.append(ChatMessage(
            role=msg_data["role"],
            content=msg_data["content"],
            timestamp=msg_data.get("timestamp")
        ))
    return messages


# ===== 함수 기반 서비스들 =====
@track_performance("recommendation_generation")
async def get_escape_room_recommendations(
    user_message: str, 
    user_prefs: Dict[str, Any],
    keywords: str = ""
) -> List[EscapeRoom]:
    """사용자 메세지에 따른 추천 방탈출 목록 반환"""
    try:
        # NOTE: 하이브리드 검색 -> tsvector 우선, pgvector 보조
        rows = await get_hybrid_recommendations(
            user_message,
            user_prefs,
            keywords
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
            f"Found {len(recommendations)} personalized recommendations: "
            f"user_prefs={user_prefs}"
        )
        
        return recommendations
        
    except Exception as e:
        logger.error(f"Personalized recommendation error: {e}")
        return []



# =============================================================================
# RAG 시스템용 핵심 함수들 (의도 분석 + 엔티티 추출 + 추천)
# =============================================================================

async def _chat(
    user_id: int, 
    session_id: str, 
    conversation_history: List[ChatMessage], 
    user_prefs: Dict,
    message: str
) -> ChatResponse:
    """RAG 기반 채팅 처리 (의도 분석 + 엔티티 추출 + 추천)"""
    
    # 사용자 메시지를 대화 기록에 추가
    user_message_obj = ChatMessage(role="user", content=message)
    conversation_history.append(user_message_obj)
    
    # 기본 선호도 설정 (user_prefs가 없으면 기본값 사용)
    if not user_prefs:
        user_prefs = {
            'experience_level': list(EXPERIENCE_LEVELS.keys())[0],
            'experience_count': 0,
            'preferred_difficulty': 2,
            'preferred_activity_level': 2,
            'preferred_regions': ["서울", "경기", "인천"],
            'preferred_sub_regions': [],
            'preferred_group_size': 2,
            'preferred_themes': [],
            'excluded_themes': [],  # 제외 테마
            'price_min': None,      # 최소 가격
            'price_max': None       # 최대 가격
        }
    
    # 경험 횟수 기반으로 선호도 정규화 (EXPERIENCE_LEVELS 활용)
    if 'experience_count' in user_prefs and user_prefs['experience_count'] is not None:
        count = user_prefs['experience_count']
        
        # experience_level 자동 설정
        user_prefs['experience_level'] = get_experience_level(count)
        
        # preferred_difficulty가 없거나 0이면 추천 난이도로 설정
        if not user_prefs.get('preferred_difficulty') or user_prefs.get('preferred_difficulty') == 0:
            level_info = EXPERIENCE_LEVELS.get(user_prefs['experience_level'], {})
            difficulties = level_info.get('recommended_difficulty', [2])
            user_prefs['preferred_difficulty'] = sum(difficulties) // len(difficulties)  # 평균값
        
        # EXPERIENCE_LEVELS에서 추천 테마도 설정 (기본값이 없을 때)
        if not user_prefs.get('preferred_themes'):
            level_info = EXPERIENCE_LEVELS.get(user_prefs['experience_level'], {})
            user_prefs['preferred_themes'] = level_info.get('recommended_themes', [])
    
    # 1. 응답 유형 분석 (어떤 종류의 응답을 원하는지)
    intent_result = await analyze_intent(message)
    
    # 2. 추출된 엔티티를 선호도에 병합 (user_prefs 직접 수정)
    extracted_entities = intent_result.get("entities", {})
    
    # 엔티티를 user_prefs에 직접 병합 (모든 키 추가)
    for key, value in extracted_entities.items():
        if value is not None:
            if key == 'preferred_difficulty' and isinstance(value, list):
                # difficulty는 리스트이므로 첫 번째 값 사용
                user_prefs[key] = value[0] if value else 2
        else:
            user_prefs[key] = value
    
    # 4. 응답 유형에 따른 처리
    response_type = intent_result.get("response_type", "general_chat")
    recommendations = []
    if response_type == "room_recommendation":
        keywords = extracted_entities.get("keywords", "")
        recommendations = await get_escape_room_recommendations(
            message, 
            user_prefs,
            keywords
        )
        
        if recommendations:
            # 추천 성공 시 RMQ 이벤트 발행 (비동기 로그 저장)
            try:
                # 추천된 방탈출 정보를 리스트로 변환
                recommendation_data = []
                for i, rec in enumerate(recommendations, 1):
                    recommendation_data.append({
                        "room_id": rec.id,
                        "rank_position": i,
                        "room_name": rec.name,
                        "theme": rec.theme,
                        "region": rec.region
                    })
                
                event_data = {
                    "user_id": user_id,
                    "session_id": session_id,
                    "action": "recommendation_log",
                    "data": {
                        "recommendations": recommendation_data,
                        "recommendation_count": len(recommendations),
                        "timestamp": now_korea_iso()
                    }
                }
                
                rmq.publish_db_sync(event_data)
                logger.debug(f"Recommendation log event published: user_id={user_id}, session_id={session_id}, count={len(recommendations)}")
                
            except Exception as e:
                logger.error(f"Failed to publish recommendation log event: {e}")
            
            rec_summary = "\n".join([
                f"• **{rec.name}** ({rec.theme}, {rec.region}, 난이도: {rec.difficulty_level}/5)"
                for rec in recommendations[:3]
            ])
            
            response_text = f"""🎯 **추천 방탈출:**

{rec_summary}

더 자세한 정보나 다른 조건으로 추천받고 싶으시면 말씀해주세요!"""
        else:
            response_text = "죄송합니다. 조건에 맞는 방탈출을 찾지 못했습니다. 다른 조건으로 시도해보시겠어요?"
    
    elif response_type == "room_inquiry":
        # 방탈출 정보 질문 처리 - LLM 기반 질문 분류
        response_text = await _handle_room_inquiry(message, conversation_history, user_prefs)
    
    else:
        # 일반 대화 처리
        response_text = await llm.generate_chat_response(
            conversation_history, 
            user_level=user_prefs.get('experience_level', list(EXPERIENCE_LEVELS.keys())[0]),
            user_preferences=user_prefs
        )
    
    # AI 응답을 대화 기록에 추가
    ai_message = ChatMessage(role="assistant", content=response_text)
    conversation_history.append(ai_message)
    await _save_conversation(user_id, conversation_history)
        
    return ChatResponse(
        message=response_text,
        session_id=session_id,
        intent=intent_result,
        user_prefs=user_prefs,
        chat_type=response_type,
        recommendations=recommendations,
        entities=extracted_entities,
        conversation_history=[msg.model_dump() for msg in conversation_history]
    )


async def chat_with_user(
    user_id: int, 
    message: str = "", 
    session_id: str | None = None
) -> ChatResponse:
    """통합 채팅 처리"""
    start_time = time.time()
    user_prefs = None  # 초기화
    
    try:
        # 1. 세션 확인/생성
        session_info = await get_or_create_user_session(user_id)
        if not session_info:
            raise CustomError("SESSION_CREATION_FAILED", "채팅 세션 생성에 실패했습니다.")

        # 2. 사용자 선호도 조회
        user_prefs = await get_user_preferences(user_id)

        # 3. 대화 기록 로드
        conversation_history = await _load_conversation(user_id)
        
        # 4. 대화 시작 (선호도는 매 대화마다 파악)
        session_id = session_info["session_id"]
        response = await _chat(
            user_id,
            session_id, 
            conversation_history, 
            user_prefs, 
            message
        )
        
        # 업데이트된 선호도는 response에서 가져옴
        user_prefs = getattr(response, 'user_prefs', user_prefs)
        
        # 응답에 세션 ID 추가
        if response:
            response.session_id = session_id
        
        # 로깅
        logger.user_action(
            str(user_id), "chat_response", "Chat response generated", 
            session_id=session_id,
            chat_type=getattr(response, 'chat_type', 'unknown'),
            is_questionnaire_active=getattr(response, 'is_questionnaire_active', False)
        )
        
        # 비즈니스 인사이트용 응답 로깅 (RMQ 비동기) - JSONB 기반
        response_time = (time.time() - start_time) * 1000
        
        # 일일 채팅 횟수 증가 및 조회
        daily_chat_count = await _increment_daily_chat_count(user_id)
        
        rmq.publish_user_action({
            "user_id": user_id,
            "session_id": session_info["session_id"],
            "action": "chat_response",
            "data": {
                "region": user_prefs.get("preferred_regions", []) if user_prefs else [],
                "theme": user_prefs.get("preferred_themes", []) if user_prefs else [],
                "message_length": len(message),
                "response_time_ms": response_time,
                "daily_chat_count": daily_chat_count,
                "has_recommendations": bool(getattr(response, 'recommendations', None))
            }
        })
        
        # 메트릭 수집
        track_chat_message(
            user_id=user_id,
            session_id=session_info["session_id"],
            message_length=len(message),
            response_time_ms=response_time,
            chat_type=getattr(response, 'chat_type', 'unknown'),
            used_rag=getattr(response, 'used_rag', False),
            success=True
        )
        
        return response
        
    except (CustomError, HTTPException) as e:
        # 에러 메트릭 수집
        response_time = (time.time() - start_time) * 1000
        
        # 에러 타입 결정
        if isinstance(e, CustomError):
            error_type = e.error_code  # "CHATBOT_ERROR", "AI_API_CALL_ERROR" 등
        else:
            error_type = f"HTTP_{e.status_code}"  # "HTTP_401", "HTTP_422" 등
    
        track_chat_message(
            user_id=user_id,
            session_id=session_id or "unknown",
            message_length=len(message),
            response_time_ms=response_time,
            chat_type="error",
            used_rag=False,
            success=False,
            error_type=error_type
        )
        track_error(error_type, "/chat", "POST", user_id)
        
        # CustomError와 HTTPException은 그대로 전파 (Global Exception Handler에서 처리)
        raise
    except Exception as e:
        # 예상치 못한 에러는 CustomError로 변환
        response_time = (time.time() - start_time) * 1000
        
        # 모든 예상치 못한 에러는 CHATBOT_ERROR로 분류
        # TODO: 
        # error_type = f"UNEXPECTED_{type(e).__name__}"
        error_type = "CHATBOT_ERROR"
        
        track_chat_message(
            user_id=user_id,
            session_id=session_id or "unknown",
            message_length=len(message),
            response_time_ms=response_time,
            chat_type="error",
            used_rag=False,
            success=False,
            error_type=error_type
        )
        track_error(error_type, "/chat", "POST", user_id)
        
        logger.error(f"Unexpected error in chat_with_user: {e}", user_id=user_id, error_type=error_type)
        raise CustomError("CHATBOT_ERROR", "챗봇 처리 중 오류가 발생했습니다.")

    finally:
        # 선호도가 업데이트되었으면 DB에 저장
        try:
            if user_prefs is not None:
                # 업데이트된 선호도가 있는지 확인 (None이 아닌 값들만 비교)
                original_prefs = await get_user_preferences(user_id) or {}
                
                # 변경사항이 있는지 확인
                has_changes = False
                for key, value in user_prefs.items():
                    if value is not None and original_prefs.get(key) != value:
                        has_changes = True
                        break
                
                if has_changes:
                    # 선호도가 변경되었으면 DB에 저장
                    await upsert_user_preferences(user_id, user_prefs)
                    logger.debug(f"User preferences updated: user_id={user_id}, changes={[k for k, v in user_prefs.items() if v is not None and original_prefs.get(k) != v]}")
        except Exception as e:
            # 선호도 저장 실패는 로그만 남기고 계속 진행
            logger.error(f"Failed to save user preferences: {e}", user_id=user_id)


async def get_or_create_user_session(user_id: int) -> Dict[str, Any] | None:
    """사용자별 세션 확인 및 생성"""
    # 1. 기존 세션이 있는지 확인
    user_session_key = f"user_session:{user_id}"
    existing_session = await redis_manager.get(user_session_key)
    
    if existing_session:
        # 기존 세션이 있으면 그걸 사용
        existing_data = json.loads(existing_session)
        return {"session_id": existing_data["session_id"], "is_new": False}
    
    # 2. 새 세션 생성
    new_session_id = str(uuid.uuid4())
    
    # Repository 함수 사용
    success = await create_session(str(user_id), new_session_id)
    if not success:
        return None

    # Redis에 통합 세션 정보 저장 
    session_data = {
        "session_id": new_session_id,
        "user_id": user_id,
        "created_at": now_korea_iso(),
        "messages": [],
        "last_activity": now_korea_iso()
    }
    
    await redis_manager.set(
        key=user_session_key,
        value=json.dumps(session_data, ensure_ascii=False),
        ex=86400  # 24시간 TTL
    )
        
    return {"session_id": new_session_id, "is_new": True}
        

async def _handle_room_inquiry(message: str, conversation_history: List[ChatMessage], user_prefs: Dict) -> str:
    """LLM 기반 방탈출 정보 질문 처리"""
    try:
        # LLM으로 질문 유형 분류
        classification_prompt = f"""
사용자의 방탈출 관련 질문을 분석하여 어떤 카테고리에 속하는지 분류해주세요.

사용자 질문: {message}

다음 카테고리 중 하나를 선택하세요:
1. "basic_info" - 방탈출 기본 개념 설명
2. "difficulty" - 난이도 관련 질문
3. "price" - 가격 관련 질문
4. "rules" - 규칙이나 방법 관련 질문
5. "themes" - 테마나 장르 관련 질문
6. "tips" - 팁이나 조언 관련 질문
7. "other" - 기타 질문

카테고리만 답변해주세요:
"""
        
        # LLM으로 분류
        response = await llm.llm.agenerate([[HumanMessage(content=classification_prompt)]])
        category = response.generations[0][0].text.strip().lower()
        
        # 카테고리별 답변 생성
        if category == "basic_info":
            return """🎯 **방탈출이란?**

방탈출은 제한된 시간 내에 주어진 공간에서 퍼즐을 풀고 단서를 찾아 탈출하는 게임이에요!

**주요 특징:**
• **시간 제한**: 보통 60-120분
• **팀워크**: 2-6명이 함께 참여
• **다양한 테마**: 추리, 공포, 판타지, SF 등
• **난이도**: 1-5단계 (초보자~전문가)

**어떤 테마가 좋을까요?** 처음이시라면 추리나 로맨스 테마를 추천해드려요! 😊"""
        
        elif category == "difficulty":
            return """🔒 **방탈출 난이도 가이드**

**1단계 (🔒)**: 초보자용
• 기본적인 퍼즐과 힌트 제공
• 방생아~방린이 추천

**2단계 (🔒🔒)**: 쉬움
• 약간의 사고력 필요
• 방린이~방소년 추천

**3단계 (🔒🔒🔒)**: 보통
• 논리적 사고와 팀워크 필요
• 방소년~방어른 추천

**4단계 (🔒🔒🔒🔒)**: 어려움
• 복잡한 퍼즐과 높은 집중력 필요
• 방어른~방신 추천

**5단계 (🔒🔒🔒🔒🔒)**: 최고 난이도
• 전문가용, 매우 복잡한 퍼즐
• 방신~방장로 추천

어떤 난이도로 도전해보고 싶으신가요? 🤔"""
        
        elif category == "price":
            return """💰 **방탈출 가격 안내**

**일반적인 가격대:**
• **1-2명**: 15,000-25,000원/인
• **3-4명**: 12,000-20,000원/인  
• **5-6명**: 10,000-18,000원/인

**지역별 차이:**
• **강남/홍대/건대/신촌**: 중간 가격에 형성 (20,000-40,000원)
• **기타 지역**: 저렴 (24,000-30,000원)

**예산에 맞는 추천을 받고 싶으시면 말씀해주세요!** 💡"""
        
        elif category == "rules":
            return """📋 **방탈출 기본 규칙**

**게임 진행:**
• 제한 시간 내에 방에서 탈출하는 것이 목표
• 팀원들과 함께 단서를 찾고 퍼즐을 풀어야 해요
• 힌트를 요청할 수 있어요 (보통 3-5회)

**주의사항:**
• 물건을 망가뜨리거나 벽에 낙서하면 안 돼요
• 스태프의 안내를 잘 들어주세요
• 휴대폰은 사용할 수 없어요

**성공 팁:**
• 팀워크가 가장 중요해요!
• 서로 다른 관점에서 생각해보세요
• 포기하지 말고 계속 도전하세요

더 궁금한 점이 있으시면 언제든 물어보세요! 😊"""
        
        elif category == "themes":
            return """🎭 **방탈출 테마 가이드**

**인기 테마들:**
• **추리**: 논리적 사고와 관찰력이 중요
• **공포**: 스릴과 긴장감을 원한다면
• **로맨스**: 부부나 연인에게 추천
• **판타지**: 마법과 환상의 세계
• **SF**: 미래적이고 과학적인 요소
• **역사**: 과거 시대 배경의 스토리
• **기타**: 기타 테마 (e.g. 이색 테마 - 치킨, 조선시대, 피자, 동물로 환생)

**테마별 추천:**
• **초보자**: 추리, 로맨스, 판타지
• **중급자**: SF, 모험, 스릴러
• **고급자**: 공포, 잠입, 타임어택

어떤 테마에 관심이 있으신가요? 🤔"""
        
        elif category == "tips":
            return """💡 **방탈출 성공 팁**

**팀 구성:**
• 2-4명이 가장 적당해요
• 서로 다른 성격의 사람들과 함께
• 리더 역할을 정해두세요

**게임 중:**
• 모든 단서를 꼼꼼히 살펴보세요
• 소통을 자주 하세요
• 시간을 체크하며 진행하세요
• 힌트를 적절히 활용하세요

**마음가짐:**
• 포기하지 마세요!
• 실수해도 괜찮아요
• 즐기는 것이 가장 중요해요

화이팅! 좋은 결과 있으시길 바라요! 🍀"""
        
        else:
            # 기타 질문은 일반 LLM으로 처리
            return await llm.generate_chat_response(
                conversation_history, 
                user_level=user_prefs.get('experience_level', list(EXPERIENCE_LEVELS.keys())[0]),
                user_preferences=user_prefs
            )
        
    except Exception as e:
        logger.error(f"Failed to handle room inquiry: {e}")
        # 에러 시 기본 LLM 응답
        return await llm.generate_chat_response(
            conversation_history, 
            user_level=user_prefs.get('experience_level', list(EXPERIENCE_LEVELS.keys())[0]),
            user_preferences=user_prefs
        )

