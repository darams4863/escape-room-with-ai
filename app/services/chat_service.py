"""방탈출 챗봇 대화 전담 서비스 (함수 기반)"""

import re
import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any

from ..core.logger import logger
from ..core.connections import redis_manager
from ..core.constants import EXPERIENCE_LEVELS, get_experience_level
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings

from ..core.config import settings
from ..core.exceptions import CustomError
from ..models.escape_room import ChatResponse, ChatMessage

from ..repositories.chat_repository import (
    create_session, 
    update_session,
    get_session_by_id,
    update_session_activity
)
from ..repositories.user_repository import upsert_user_preferences
from .nlp_service import analyze_intent

# LLM 관련만 클래스로 유지 (상태 관리 필요)
class LLMService:
    """LLM 및 임베딩 서비스 (상태 유지 필요)"""
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-3.5-turbo",
            temperature=0.7,
            openai_api_key=settings.openai_api_key
        )
        self.embeddings = OpenAIEmbeddings(openai_api_key=settings.openai_api_key)
        
        # 경험 등급별 프롬프트 템플릿
        self.chat_prompt = PromptTemplate(
            input_variables=["conversation_history", "user_message", "user_level", "user_preferences"],
            template="""
당신은 방탈출 전문가입니다. 사용자의 경험 등급과 선호사항을 고려하여 맞춤형 추천을 해주세요.

사용자 정보:
- 경험 등급: {user_level}
- 선호사항: {user_preferences}

대화 기록:
{conversation_history}

사용자 메시지: {user_message}

다음 단계를 따라 응답해주세요:

1. 사용자의 메시지를 이해하고 공감해주세요
2. 경험 등급에 맞는 톤으로 응답하세요:
   - 방생아/방린이: 친절한 가이드 톤
   - 방소년/방어른: 동료 게이머 톤  
   - 방신/방장로: 존중 + 도전 욕구 자극
3. 선호사항을 파악하고 반영하세요 (난이도, 활동성, 지역, 연령대, 그룹 크기 등)
4. 적절한 방탈출을 추천해주세요
5. 경험 등급에 맞는 조언을 포함하세요

응답 형식:
- 메시지: 사용자와의 자연스러운 대화
- 추천 방탈출: 2-3개 추천 (있다면)
- 사용자 프로필: 파악된 정보 요약

응답해주세요:
"""
        )
        
        self.chain = LLMChain(llm=self.llm, prompt=self.chat_prompt)
    
    async def generate_response(self, conversation_history: List[ChatMessage], user_level: str, user_prefs: Dict) -> str:
        """LLM을 사용하여 경험 등급별 맞춤 응답 생성"""
        try:
            # 대화 기록을 문자열로 변환
            history_text = "\n".join([
                f"{msg.role}: {msg.content}" for msg in conversation_history[-6:]
            ])
            
            # 사용자 선호사항 요약
            prefs_summary = _format_user_preferences(user_prefs)
            
            # 랭체인 실행
            response = await self.chain.arun(
                conversation_history=history_text,
                user_message=conversation_history[-1].content,
                user_level=user_level,
                user_preferences=prefs_summary
            )
            
            return response.strip()
            
        except Exception as e:
            logger.error(f"Response generation error: {e}")
            raise CustomError("OPENAI_ERROR")
    
    async def create_embedding(self, text: str) -> List[float]:
        """텍스트를 임베딩 벡터로 변환"""
        try:
            return await self.embeddings.aembed_query(text)
        except Exception as e:
            logger.error(f"Embedding creation error: {e}")
            raise CustomError("EMBEDDING_ERROR")

# LLM 서비스 인스턴스
llm_service = LLMService()

# ===== 헬퍼 함수들 =====

async def _save_conversation(session_id: str, conversation_history: List[ChatMessage]):
    """대화 저장 (Redis + PostgreSQL)"""
    messages_data = []
    for msg in conversation_history:
        timestamp = msg.timestamp.isoformat() if msg.timestamp else datetime.now(timezone.utc).isoformat()
        messages_data.append({
            "role": msg.role,
            "content": msg.content,
            "timestamp": timestamp
        })
    
    # Redis + PostgreSQL 저장
    await redis_manager.set(f"chat_session:{session_id}", json.dumps(messages_data, ensure_ascii=False), ex=86400)
    await update_session(session_id, json.dumps({"messages": messages_data}, ensure_ascii=False))

async def _load_conversation(session_id: str) -> List[ChatMessage]:
    """대화 로드 (Redis → PostgreSQL → Redis 캐시)"""
    # Redis에서 시도
    conversation_data = await redis_manager.get(f"chat_session:{session_id}")
    if conversation_data:
        return _parse_messages(json.loads(conversation_data))
    
    # PostgreSQL에서 로드
    session_data = await get_session_by_id(session_id)
    if session_data:
        conversation_data = session_data['conversation_history']
        # Redis에 캐시
        await redis_manager.set(f"chat_session:{session_id}", conversation_data, ex=86400)
        return _parse_messages(json.loads(conversation_data))
    
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

# 선호도 파악 단계 정의
PREFERENCE_STEPS = {
    "experience_check": {
        "next": "experience_count",
        "question": "방탈출은 해보신 적 있나요?",
        "options": ["네, 해봤어요!", "아니요, 처음이에요."],
        "field": "experience_level"
    },
    "experience_count": {
        "next": "difficulty_check",
        # cf. 방생아 > 방린이 > 방소년 > 방어른 > 방신 > 방장로
        "question": "몇 번 정도 해보셨어요?",
        "options": ["1-10회", "11-30회", "31-50회", "51-80회", "81-100회", "100회 이상"],
        "field": "experience_count"
    },
    "difficulty_check": {
        "next": "activity_level_check", 
        "question": "어떤 난이도를 선호하시나요?",
        "options": ["🔒", "🔒🔒", "🔒🔒🔒", "🔒🔒🔒🔒", "🔒🔒🔒🔒🔒"],
        "field": "preferred_difficulty"
    },
    "activity_level_check": {
        "next": "group_size_check",
        "question": "활동성을 선호하시나요?",
        "options": ["거의 없음", "보통", "많음"],
        "field": "preferred_activity_level"
    },
    "group_size_check": {
        "next": "region_check",
        "question": "그룹 크기는 2-4명 중에 어떤 것을 선호하시나요?",
        "options": ["2명", "3명", "4명"],
        "field": "preferred_group_size"
    },
    "region_check": {
        "next": "themes_check",
        "question": "선호하시는 지역이 있나요?",
        "options": ["서울", "경기", "부산", "대구", "인천"],
        "field": "preferred_regions"
    },
    "themes_check": {
        "next": None,  # 마지막 단계
        "question": "어떤 테마를 선호하시나요? 여러개도 선택 가능합니다!",
        "options": ["스릴러", "기타", "판타지", "추리", "호러/공포", "잠입", "모험/탐험", "감성", "코미디", "드라마", "범죄", "미스터리", "SF", "19금", "액션", "역사", "로맨스", "아이", "타임어택"],
        "field": "preferred_themes"
    }
}

async def chat_with_user(session_id: str, user_prefs: Dict, user_message: str, user_id: int) -> ChatResponse:    
    """통합 채팅 처리 - 선호도 파악 + 방탈출 추천"""
        # 대화 기록 로드
    conversation_history = await _load_conversation(session_id)
    
    # 1. 선호도 파악이 필요한 경우
    if not user_prefs or not _is_preferences_complete(user_prefs):
        return await handle_preference_flow(
            user_id, session_id, conversation_history, user_prefs, user_message
        )
    
    # 2. 선호도가 완성된 경우 - 방탈출 추천 대화
    return await handle_general_chat(user_id, session_id, conversation_history, user_message, user_prefs)

def _is_preferences_complete(user_prefs: Dict) -> bool:
    """사용자 선호도가 완성되었는지 확인"""
    if not user_prefs:
        return False
    
    required_fields = [
        'experience_level',
        'preferred_difficulty', 
        'preferred_activity_level',
        'preferred_regions',
        'preferred_group_size',
        'preferred_themes'
    ]
    
    for field in required_fields:
        if not user_prefs.get(field):
            return False
    
    return True

async def handle_preference_flow(
    user_id: int, 
    session_id: str, 
    conversation_history: List[ChatMessage], 
    user_prefs: Dict,
    user_message: str
) -> ChatResponse:
    """단계별 선호도 파악 플로우"""
    # 현재 진행 중인 단계 확인
    current_step = await _get_current_preference_step(session_id)
    
    # 사용자가 이전 질문에 답변한 경우
    if current_step and user_message:
        return await _process_preference_answer(
            user_id, session_id, conversation_history, current_step, user_message, user_prefs
        )
    
    # 새로운 선호도 파악 시작 또는 다음 질문
    return await _get_next_preference_question(
        user_id, session_id, current_step, user_prefs
    )

async def _get_current_preference_step(session_id: str) -> str | None:
    """현재 진행 중인 선호도 파악 단계 조회"""
    return await redis_manager.get(f"preference_step:{session_id}")
        
async def _get_next_preference_question(
    user_id: int, 
    session_id: str, 
    current_step: str | None, 
    user_prefs: Dict
) -> ChatResponse:
    """다음 선호도 질문 반환"""
    
    # 첫 번째 질문인 경우
    if not current_step:
        next_step = "experience_check"
        await _set_current_preference_step(session_id, next_step)
        
        # AI 질문을 대화 기록에 추가
        ai_message = ChatMessage(
            role="assistant",
            content=_get_greeting_message()
        )
        conversation_history = [ai_message]
        await _save_conversation(session_id, conversation_history)
        
        return ChatResponse(
            message=_get_greeting_message(),
            session_id=session_id,
            questionnaire={
                "type": next_step,
                "question": PREFERENCE_STEPS[next_step]["question"],
                "options": PREFERENCE_STEPS[next_step]["options"],
                "next_step": PREFERENCE_STEPS[next_step]["next"]
            },
            chat_type="preference_start",
            is_questionnaire_active=True
        )
        
    # 다음 단계로 진행
    next_step = PREFERENCE_STEPS.get(current_step, {}).get("next")
    if next_step:
        await _set_current_preference_step(session_id, next_step)
        
        # AI 질문을 대화 기록에 추가
        ai_message = ChatMessage(
            role="assistant",
            content="좋습니다! 다음 질문입니다."
        )
        conversation_history = [ai_message]
        await _save_conversation(session_id, conversation_history)
        
        return ChatResponse(
            message="좋습니다! 다음 질문입니다.",
            session_id=session_id,
            questionnaire={
                "type": next_step,
                "question": PREFERENCE_STEPS[next_step]["question"],
                "options": PREFERENCE_STEPS[next_step]["options"],
                "next_step": PREFERENCE_STEPS[next_step].get("next")
            },
            chat_type="preference_question",
            is_questionnaire_active=True
        )
        
    # 모든 질문 완료
    return await _complete_preferences(user_id, session_id, user_prefs)

async def _process_preference_answer(
    user_id: int, 
    session_id: str, 
    conversation_history: List[ChatMessage],
    current_step: str, 
    user_answer: str, 
    user_prefs: Dict
) -> ChatResponse:
    """사용자 답변 처리 및 다음 단계 결정"""
    user_message = ChatMessage(
        role="user",
        content=user_answer
    )
    conversation_history.append(user_message)
    
    # 답변을 선호도에 저장
    await _save_preference_answer(user_id, current_step, user_answer, user_prefs)
    
    # 대화 기록 저장
    await _save_conversation(session_id, conversation_history)
    
    # 다음 단계 결정
    next_step = PREFERENCE_STEPS.get(current_step, {}).get("next")
    
    if next_step:
        # 다음 질문으로 진행
        await _set_current_preference_step(session_id, next_step)
        return await _get_next_preference_question(user_id, session_id, next_step, user_prefs)
    else:
        # 모든 질문 완료
        return await _complete_preferences(user_id, session_id, user_prefs)

async def _complete_preferences(user_id: int, session_id: str, user_prefs: Dict) -> ChatResponse:
    """선호도 파악 완료 및 일반 대화 시작"""
    # Redis에서 진행 단계 제거
    await redis_manager.delete(f"preference_step:{session_id}")
    
    # 완료 메시지
    completion_message = (
        "모든 선호도 파악이 완료되었습니다!\n\n"
        "이제 당신에게 딱 맞는 방탈출을 추천해드릴게요!\n"
        "어떤 방탈출을 찾고 계신가요?\n\n"
        "예시:\n"
        "- '강남에서 3인 방탈출 추천해줘'\n"
        "- '추리 테마로 활동성 높은 방탈출 추천해줘'\n"
        "- '난이도 높은 방탈출 추천해줘'"
    )
    
    return ChatResponse(
        message=completion_message,
        session_id=session_id,
        questionnaire=None,
        chat_type="preference_complete",
        is_questionnaire_active=False
    )

async def _set_current_preference_step(session_id: str, step: str):
    """현재 선호도 파악 단계를 Redis에 저장"""
    await redis_manager.set(
        key=f"preference_step:{session_id}",
        value=step,
        ex=3600  # 1시간 TTL
    )

def _get_greeting_message() -> str:
    """사용자 인사 메시지 생성"""
    return (
        f"안녕하세요! 🎉 **AI 방탈출 월드**에 오신 것을 환영합니다!\n\n"
        "저는 당신에게 딱 맞는 방탈출을 추천해드리는 AI입니다!\n\n"
                    "**방탈출은 해보신 적 있나요?**\n"
                    "경험에 따라 맞춤형 추천을 해드릴게요! 😊"
                )
                
async def _save_preference_answer(user_id: int, step: str, answer: str, user_prefs: Dict):
    """사용자 답변을 선호도에 저장"""
    step_info = PREFERENCE_STEPS.get(step, {})
    field = step_info.get("field")
    
    if not field:
        return
    
    # 답변을 적절한 값으로 변환
    if step == "experience_check":
        if "해봤" in answer or "네" in answer:
            user_prefs[field] = "방소년"  # 기본값
        else:
            user_prefs[field] = "방생아"
            
    elif step == "experience_count":
        # 경험 횟수 범위를 숫자로 변환하고 경험 등급도 함께 설정
        if "1-10" in answer:
            user_prefs[field] = 5
            user_prefs['experience_level'] = "방린이"
        elif "11-30" in answer:
            user_prefs[field] = 20
            user_prefs['experience_level'] = "방소년"
        elif "31-50" in answer:
            user_prefs[field] = 40
            user_prefs['experience_level'] = "방어른"
        elif "51-80" in answer:
            user_prefs[field] = 65
            user_prefs['experience_level'] = "방신"
        elif "81-100" in answer:
            user_prefs[field] = 90
            user_prefs['experience_level'] = "방장로"
        elif "100회 이상" in answer:
            user_prefs[field] = 120
            user_prefs['experience_level'] = "방장로"
        else:
            user_prefs[field] = 1
            user_prefs['experience_level'] = "방생아"
            
    elif step == "difficulty_check":
        # 이모지 개수로 난이도 결정
        difficulty = answer.count("🔒")
        user_prefs[field] = difficulty
        
    elif step == "activity_level_check":
        if answer == "거의 없음":
            user_prefs[field] = 1
        elif answer == "보통":
            user_prefs[field] = 2
        elif answer == "많음":
            user_prefs[field] = 3
            
    elif step == "group_size_check":
        # "2명", "3명", "4명"에서 숫자 추출
        numbers = re.findall(r'\d+', answer)
        if numbers:
            user_prefs[field] = int(numbers[0])
            
    elif step == "region_check":
        # 선택된 지역을 리스트로 저장
        user_prefs[field] = [answer]
        
    elif step == "themes_check":
        # 선택된 테마를 리스트로 저장
        user_prefs[field] = [answer]
    
    # 선호도 업데이트
    await upsert_user_preferences(user_id, user_prefs)

async def get_or_create_user_session(user_id: int, session_id: str | None = None) -> Dict[str, Any] | None:
    """사용자별 세션 확인 및 생성 (하나만 허용)"""
    # 1. 기존 세션이 있는지 확인
    existing_session_key = f"user_session:{user_id}"
    existing_session = await redis_manager.get(existing_session_key)
    
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
        
    # Redis에 세션 정보 저장 (하나만)
    session_data = {
        "session_id": new_session_id,
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await redis_manager.set(
        
        key=existing_session_key,
        value=json.dumps(session_data, ensure_ascii=False),
        ex=86400  # 24시간 TTL
    )
        
    return {"session_id": new_session_id, "is_new": True}
        
async def handle_general_chat(
    user_id: int, 
    session_id: str, 
    conversation_history: List[ChatMessage],
    user_message: str, 
    user_prefs: Dict, 
) -> ChatResponse:
    """방탈출 추천을 위한 일반 챗봇 대화 처리"""
    # 사용자 메시지 추가
    user_message_obj = ChatMessage(
        role="user",
        content=user_message
    )
    conversation_history.append(user_message_obj)
    
    # 사용자 의도 파악 (실제 LLM 기반)
    user_intent = await _analyze_user_intent_with_llm(user_message, conversation_history)
    
    # 의도별 처리 로직
    if user_intent["intent"] == "recommendation":
        return await _handle_recommendation_request(
            session_id, user_id, user_message, user_prefs, conversation_history, user_intent
        )
    elif user_intent["intent"] in ["question", "general_chat"]:
        # question, general_chat, 기타 모든 경우를 하나로 통합
        return await _handle_general_response(
            session_id, user_id, user_message, user_prefs, conversation_history, user_intent
        )
    else:
        # 의도 파악 실패 시 명확한 질문
        return await _handle_unclear_intent(session_id, user_message)


async def _analyze_user_intent_with_llm(user_message: str, conversation_history: List[ChatMessage]) -> Dict:
    """LLM을 사용한 실제 의도 분석"""
    intent_result = await analyze_intent(user_message)
    
    # 결과에 대화 맥락 정보 추가
    intent_result["conversation_context"] = [
        {
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
        }
        for msg in conversation_history[-6:]  # 최근 6개 메시지만 (토큰 절약)
    ]
    
    return intent_result


async def _handle_recommendation_request(
    session_id: str, 
    user_id: int, 
    user_message: str, 
    user_prefs: Dict, 
    conversation_history: List[ChatMessage],
    user_intent: Dict
) -> ChatResponse:
    """방탈출 추천 요청 처리"""
    # 엔티티에서 추천 조건 추출
    entities = user_intent.get("entities", {})
    
    # 실제 방탈출 추천 조회
    from ..services.recommendation_service import get_escape_room_recommendations
    recommendations = await get_escape_room_recommendations(user_message, user_prefs)
    
    # 추천 결과를 포함한 응답 생성
    if recommendations:
        # 추천 결과 요약
        rec_summary = "\n".join([
            f"• {rec.name} ({rec.theme}, {rec.region}, 난이도: {rec.difficulty_level})"
            for rec in recommendations[:3]
        ])
        
        response_text = f"""
{entities.get('region', '')}에서 {entities.get('theme', '')} 테마로 추천해드릴게요!

🎯 **추천 방탈출:**
{rec_summary}

더 자세한 정보나 다른 조건으로 추천받고 싶으시면 말씀해주세요!
"""
    else:
        response_text = "죄송합니다. 조건에 맞는 방탈출을 찾지 못했습니다. 다른 조건으로 시도해보시겠어요?"
    
    # 응답 저장 및 반환
    ai_message = ChatMessage(role="assistant", content=response_text)
    conversation_history.append(ai_message)
    await _save_conversation(session_id, conversation_history)
    
    return ChatResponse(
        message=response_text,
        session_id=session_id,
        questionnaire=None,
        recommendations=recommendations[:3] if recommendations else None,
        user_profile=await extract_user_profile(conversation_history, user_prefs),
        chat_type="recommendation",
        is_questionnaire_active=False
    )
        

async def _handle_general_response(
    session_id: str, 
    user_id: int, 
    user_message: str, 
    user_prefs: Dict, 
    conversation_history: List[ChatMessage],
    user_intent: Dict
) -> ChatResponse:
    """통합된 일반 응답 처리 (질문, 일반 대화, 의도 불명 등)"""
    # 의도에 따른 적절한 응답 생성
    intent = user_intent.get("intent", "general_chat")
    
    if intent == "question":
        # 질문인 경우 더 구체적인 답변
        response_text = await llm_service.generate_response(
            conversation_history, 
            user_prefs.get('experience_level', '방생아'), 
            user_prefs
        )
        chat_type = "question"
    else:
        # 일반 대화 또는 의도 불명인 경우 친근한 응답
        response_text = await llm_service.generate_response(
            conversation_history, 
            user_prefs.get('experience_level', '방생아'), 
            user_prefs
        )
        chat_type = "general"
        
        # AI 응답 추가
    ai_message = ChatMessage(role="assistant", content=response_text)
    conversation_history.append(ai_message)
    await _save_conversation(session_id, conversation_history)
    
    return ChatResponse(
        message=response_text,
        session_id=session_id,
        questionnaire=None,
        recommendations=None,
        user_profile=await extract_user_profile(conversation_history, user_prefs),
        chat_type=chat_type,
        is_questionnaire_active=False
    )
        
async def _handle_unclear_intent(session_id: str, user_message: str) -> ChatResponse:
    """의도 파악 실패 시 처리"""
    clarification_message = f"""
죄송합니다. "{user_message}"의 의도를 정확히 파악하지 못했어요.

다음 중 어떤 것을 원하시나요?

1. 🎯 **방탈출 추천**: "강남에서 추리 테마로 추천해줘"
2. ❓ **질문**: "방탈출이 뭐예요?"
3. 💬 **일반 대화**: "안녕하세요"

구체적으로 말씀해주시면 더 정확한 도움을 드릴 수 있어요!
"""
    
    return ChatResponse(
        message=clarification_message,
        session_id=session_id,
        questionnaire={
            "type": "intent_clarification",
            "question": "어떤 것을 원하시나요?",
            "options": ["방탈출 추천", "질문", "일반 대화"],
            "next_step": "intent_confirmed"
        },
        chat_type="clarification",
        is_questionnaire_active=True
    )


def _format_user_preferences(user_prefs: Dict) -> str:
    """사용자 선호사항을 문자열로 포맷팅"""
    if not user_prefs:
        return "정보 없음"
    
    parts = []
    if user_prefs.get('experience_level'):
        parts.append(f"경험 등급: {user_prefs['experience_level']}")
    if user_prefs.get('experience_count'):
        parts.append(f"경험 횟수: {user_prefs['experience_count']}회")
    if user_prefs.get('preferred_difficulty'):
        parts.append(f"선호 난이도: {user_prefs['preferred_difficulty']}")
    if user_prefs.get('preferred_regions'):
        parts.append(f"선호 지역: {', '.join(user_prefs['preferred_regions'])}")
    
    return ", ".join(parts) if parts else "정보 없음"

async def extract_user_profile(conversation_history: List[ChatMessage], user_prefs: Dict) -> Dict[str, Any] | None:
    """대화 기록과 선호사항에서 사용자 프로필 추출"""
    # 최근 대화에서 키워드 추출
    recent_messages = " ".join([
        msg.content for msg in conversation_history[-4:] if msg.role == "user"
    ])
    
    profile = {
        "experience_level": user_prefs.get('experience_level', '방생아') if user_prefs else '방생아',
        "experience_count": user_prefs.get('experience_count'),
        "preferred_difficulty": user_prefs.get('preferred_difficulty'),
        "preferred_regions": user_prefs.get('preferred_regions', []),
        "preferred_group_size": None
    }
    
    # 그룹 사이즈 추출
    group_size = parse_group_size(recent_messages)
    if group_size:
        profile["preferred_group_size"] = group_size
    
    return profile

def parse_group_size(message: str) -> int | str | None:
    """유연한 인원수 파싱"""
    # 숫자 + "명" 패턴
    numbers_with_unit = re.findall(r'(\d+)명', message)
    if numbers_with_unit:
        return int(numbers_with_unit[0])
    
    # 단순 숫자 패턴
    if any(word in message for word in ["인원", "사람", "명", "그룹"]):
        numbers = re.findall(r'\b(\d+)\b', message)
        for num in numbers:
            num_int = int(num)
            if 1 <= num_int <= 10:
                return num_int
    
    # 한글 숫자
    korean_numbers = {
        "한": 1, "하나": 1, "혼자": 1,
        "둘": 2, "두": 2, "커플": 2, "연인": 2,
        "셋": 3, "세": 3, "삼": 3,
        "넷": 4, "네": 4, "사": 4,
        "다섯": 5, "오": 5,
        "여섯": 6, "육": 6
    }
    
    for korean, number in korean_numbers.items():
        if korean in message:
            return number
    
    return None

