"""방탈출 챗봇 대화 전담 서비스 (함수 기반)"""

import re
import uuid
import json
from ..utils.time import now_korea_iso
from typing import List, Dict, Any

from ..core.logger import logger
from ..core.connections import redis_manager
from ..core.constants import PREFERENCE_STEPS
from fastapi import HTTPException

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
)
from ..repositories.user_repository import upsert_user_preferences, get_user_preferences
from .nlp_service import (
    analyze_intent,
    analyze_experience_answer,
    analyze_experience_count,
    analyze_difficulty_answer,
    analyze_activity_answer,
    analyze_group_size_answer,
    analyze_region_answer,
    analyze_theme_answer
)

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
        
        # LLMChain 대신 최신 방식 사용
        self.chain = self.chat_prompt | self.llm
    
    async def generate_response(self, conversation_history: List[ChatMessage], user_level: str, user_prefs: Dict) -> str:
        """LLM을 사용하여 경험 등급별 맞춤 응답 생성"""
        try:
            # 대화 기록을 문자열로 변환
            history_text = "\n".join([
                f"{msg.role}: {msg.content}" for msg in conversation_history[-6:]
            ])
            
            # 사용자 선호사항 요약
            prefs_summary = _format_user_preferences(user_prefs)
            
            # 랭체인 실행 (최신 방식)
            response = await self.chain.ainvoke({
                "conversation_history": history_text,
                "user_message": conversation_history[-1].content,
                "user_level": user_level,
                "user_preferences": prefs_summary
            })
            
            return response.content.strip()
            
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
        timestamp = msg.timestamp.isoformat() if msg.timestamp else now_korea_iso()
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



async def chat_with_user(
    user_id: int,
    message: str,
    session_id: str | None = None
) -> ChatResponse:
    """통합 채팅 처리 - 선호도 파악 + 방탈출 추천 (Service 계층에서 예외 처리)"""
    try:
        # 입력 검증
        if not message or not message.strip():
            raise CustomError("VALIDATION_ERROR", "메시지를 입력해주세요.")
        
        if len(message) > 500:
            raise CustomError("VALIDATION_ERROR", "메시지가 너무 깁니다. (최대 500자)")
        
        # XSS 방지: 기본적인 HTML 태그 제거
        sanitized_message = re.sub(r'<[^>]+>', '', message.strip())
        
        # Rate Limiting 체크
        is_allowed, status = await redis_manager.rate_limit_check(user_id, limit=20, window=60)
        if not is_allowed:
            raise CustomError("RATE_LIMIT_EXCEEDED", f"요청 한도를 초과했습니다. {status.get('reset_time', 60)}초 후 다시 시도해주세요.")
        
        # 로깅
        logger.user_action(str(user_id), "chat_request", f"Chat request: {sanitized_message[:50]}...")
        
        # 사용자 챗 세션 확인 및 생성 
        session_info = await get_or_create_user_session(user_id, session_id)
        if not session_info:
            raise CustomError("SESSION_CREATION_FAILED", "채팅 세션 생성에 실패했습니다.")

        # 유저 기본 선호도 조회
        user_prefs = await get_user_preferences(user_id)

        # 대화 기록 로드
        conversation_history = await _load_conversation(session_info["session_id"])
        
        # 1. 선호도 파악이 필요한 경우
        if not user_prefs or not _is_preferences_complete(user_prefs):
            response = await handle_preference_flow(
                user_id, session_info["session_id"], conversation_history, user_prefs, sanitized_message
            )
        else:
            # 2. 선호도가 완성된 경우 - 방탈출 추천 대화
            response = await handle_general_chat(user_id, session_info["session_id"], conversation_history, sanitized_message, user_prefs)
        
        # 응답에 세션 ID 추가
        if response:
            response.session_id = session_info["session_id"]
        
        # 로깅
        logger.user_action(
            str(user_id), "chat_response", "Chat response generated", 
            session_id=session_info["session_id"],
            chat_type=getattr(response, 'chat_type', 'unknown'),
            is_questionnaire_active=getattr(response, 'is_questionnaire_active', False)
        )
        
        return response
        
    except (CustomError, HTTPException):
        # CustomError와 HTTPException은 그대로 전파 (Global Exception Handler에서 처리)
        raise
    except Exception as e:
        # 예상치 못한 에러는 CustomError로 변환
        logger.error(f"Unexpected error in chat_with_user: {e}", user_id=user_id, error_type="unexpected_error")
        raise CustomError("CHATBOT_ERROR", "챗봇 처리 중 오류가 발생했습니다.")

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
    """현재 진행 중인 선호도 파악 단계 조회 (Redis 우선, 실패 시 DB 복구)"""
    try:
        # 1. Redis에서 먼저 확인
        current_step = await redis_manager.get(f"preference_step:{session_id}")
        if current_step:
            logger.info(f"Found preference step in Redis: {current_step}")
            return current_step
        
        # 2. Redis에 없으면 DB에서 복구 시도
        logger.info("No preference step in Redis, attempting DB recovery...")
        return await _recover_preference_step_from_db(session_id)
        
    except Exception as e:
        logger.error(f"Error getting preference step: {e}")
        return None

async def _recover_preference_step_from_db(session_id: str) -> str | None:
    """DB의 conversation_history에서 선호도 진행 상황 복구"""
    try:
        # 대화 기록 로드
        conversation_history = await _load_conversation(session_id)
        if not conversation_history:
            logger.info("No conversation history found, starting fresh")
            return None
        
        # 대화 기록에서 선호도 관련 질문/답변 분석
        completed_steps = _analyze_conversation_for_preference_steps(conversation_history)
        
        if not completed_steps:
            logger.info("No preference steps found in conversation, starting fresh")
            return None
        
        # 마지막 완료된 단계의 다음 단계 결정
        last_completed = completed_steps[-1]
        next_step = PREFERENCE_STEPS.get(last_completed, {}).get("next")
        
        if next_step:
            logger.info(f"Recovered preference progress: {last_completed} -> {next_step}")
            # Redis에 복구된 단계 저장
            await _set_current_preference_step(session_id, next_step)
            return next_step
        else:
            # 모든 단계 완료
            logger.info("All preference steps completed based on conversation history")
            return None
        
    except Exception as e:
        logger.error(f"Failed to recover preference step from DB: {e}")
        return None

def _analyze_conversation_for_preference_steps(conversation_history: List[ChatMessage]) -> List[str]:
    """대화 기록에서 완료된 선호도 단계들 분석"""
    completed_steps = []
    
    # 각 단계별 질문과 답변 패턴 매칭
    step_patterns = {
        "experience_check": {
            "question": "방탈출은 해보신 적 있나요?",
            "answers": ["네, 해봤어요!", "아니요, 처음이에요."]
        },
        "experience_count": {
            "question": "몇 번 정도 해보셨나요?",
            "answers": ["1-10회", "11-30회", "31-50회", "51-80회", "81-100회", "100회 이상"]
        },
        "difficulty_check": {
            "question": "어떤 난이도를 선호하시나요?",
            "answers": ["🔒", "🔒🔒", "🔒🔒🔒"]
        },
        "activity_level_check": {
            "question": "활동성 수준은 어떻게 하시나요?",
            "answers": ["거의 없음", "보통", "많음"]
        },
        "group_size_check": {
            "question": "몇 명이서 가시나요?",
            "answers": ["2명", "3명", "4명", "5명 이상"]
        },
        "region_check": {
            "question": "어느 지역을 선호하시나요?",
            "answers": ["강남", "홍대", "건대", "신촌", "기타"]
        },
        "themes_check": {
            "question": "어떤 테마를 선호하시나요?",
            "answers": ["추리", "공포", "판타지", "SF", "스릴러", "모험"]
        }
    }
    
    # 대화 기록을 순회하며 질문-답변 쌍 찾기
    for i in range(len(conversation_history) - 1):
        assistant_msg = conversation_history[i]
        user_msg = conversation_history[i + 1]
        
        if assistant_msg.role == "assistant" and user_msg.role == "user":
            assistant_content = assistant_msg.content
            user_content = user_msg.content
            
            # 각 단계별로 질문과 답변 매칭 확인
            for step, pattern in step_patterns.items():
                if step in completed_steps:
                    continue
                    
                # 질문 패턴 매칭
                question_match = any(
                    pattern["question"] in assistant_content or 
                    q in assistant_content for q in [pattern["question"]]
                )
                
                # 답변 패턴 매칭
                answer_match = any(
                    answer in user_content for answer in pattern["answers"]
                )
                
                if question_match and answer_match:
                    completed_steps.append(step)
                    logger.info(f"Found completed step: {step} (Q: {assistant_content[:50]}... A: {user_content})")
                    break
    
    return completed_steps
        
async def _get_next_preference_question(
    user_id: int, 
    session_id: str, 
    current_step: str | None, 
    user_prefs: Dict
) -> ChatResponse:
    """다음 선호도 질문 반환 (복구 로직 포함)"""
    
    # Redis에서 진행 단계가 없으면 DB에서 복구 시도
    if not current_step:
        current_step = await _recover_preference_step_from_db(session_id)
    
    # 첫 번째 질문인 경우
    if not current_step:
        return await _handle_first_preference_question(session_id)
        
    # 다음 단계로 진행
    next_step = PREFERENCE_STEPS.get(current_step, {}).get("next")
    if next_step:
        return await _handle_next_preference_question(session_id, current_step, next_step)
        
    # 모든 질문 완료
    return await _complete_preferences(user_id, session_id, user_prefs)

async def _handle_first_preference_question(session_id: str) -> ChatResponse:
    """첫 번째 선호도 질문 처리"""
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

async def _handle_next_preference_question(session_id: str, current_step: str, next_step: str) -> ChatResponse:
    """다음 선호도 질문 처리"""
    await _set_current_preference_step(session_id, next_step)
    
    # 복구된 경우와 새로 진행하는 경우 메시지 구분
    message = _get_next_question_message(current_step)
    
    # AI 질문을 대화 기록에 추가
    ai_message = ChatMessage(
        role="assistant",
        content=message
    )
    conversation_history = [ai_message]
    await _save_conversation(session_id, conversation_history)
    
    return ChatResponse(
        message=message,
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

def _get_next_question_message(current_step: str) -> str:
    """다음 질문 메시지 생성"""
    if current_step in ["experience_check", "experience_count", "difficulty_check", 
                       "activity_level_check", "group_size_check", "region_check"]:
        return "좋습니다! 다음 질문입니다."
    else:
        return "이어서 다음 질문을 드릴게요."

async def _process_preference_answer(
    user_id: int, 
    session_id: str, 
    conversation_history: List[ChatMessage],
    current_step: str, 
    user_answer: str, 
    user_prefs: Dict
) -> ChatResponse:
    """사용자 답변 처리 및 다음 단계 결정 (예외 처리 강화)"""
    try:
        user_message = ChatMessage(
            role="user",
            content=user_answer
        )
        conversation_history.append(user_message)
        
        # 답변을 선호도에 저장 (예외 처리 포함)
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
            
    except CustomError as e:
        # 선호도 저장 실패 시 사용자에게 재시도 요청
        logger.error(f"Preference processing failed: {e.message}")
        return ChatResponse(
        message=f"죄송합니다. 답변 저장에 실패했습니다. 다시 시도해주세요.\n\n{user_answer}",
            session_id=session_id,
            questionnaire={
            "type": current_step,
            "question": PREFERENCE_STEPS[current_step]["question"],
            "options": PREFERENCE_STEPS[current_step]["options"],
            "next_step": PREFERENCE_STEPS[current_step].get("next")
        },
        chat_type="preference_retry",
            is_questionnaire_active=True
        )
    except Exception as e:
        # 기타 예외 발생 시
        logger.error(f"Unexpected error in preference processing: {e}")
        return ChatResponse(
            message="죄송합니다. 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            session_id=session_id,
            questionnaire=None,
            chat_type="error",
            is_questionnaire_active=False
        )

async def _complete_preferences(user_id: int, session_id: str, user_prefs: Dict) -> ChatResponse:
    """선호도 파악 완료 및 일반 대화 시작 (예외 처리 포함)"""
    try:
        # Redis에서 진행 단계 제거
        await redis_manager.delete(f"preference_step:{session_id}")
        logger.info(f"Preference flow completed for user {user_id}, session {session_id}")
        
        # 완료 메시지
        completion_message = (
            "🎉 **모든 선호도 파악이 완료되었습니다!**\n\n"
            "이제 당신에게 딱 맞는 방탈출을 추천해드릴게요!\n"
            "어떤 방탈출을 찾고 계신가요?\n\n"
            "**예시:**\n"
            "- '강남에서 3인 방탈출 추천해줘'\n"
            "- '추리 테마로 활동성 높은 방탈출 추천해줘'\n"
            "- '난이도 높은 방탈출 추천해줘'"
        )
        
        # 완료 메시지를 대화 기록에 추가
        ai_message = ChatMessage(
            role="assistant",
            content=completion_message
        )
        await _save_conversation(session_id, [ai_message])
        
        return ChatResponse(
            message=completion_message,
            session_id=session_id,
            questionnaire=None,
            chat_type="preference_complete",
            is_questionnaire_active=False
        )
        
    except Exception as e:
        logger.error(f"Error completing preferences: {e}")
        # 완료 처리 실패해도 사용자에게는 성공 메시지 표시
        return ChatResponse(
            message="선호도 파악이 완료되었습니다! 이제 방탈출을 추천해드릴게요.",
            session_id=session_id,
            questionnaire=None,
            chat_type="preference_complete",
            is_questionnaire_active=False
        )
        
async def _set_current_preference_step(session_id: str, step: str):
    """현재 선호도 파악 단계를 Redis에 저장"""
    try:
        await redis_manager.set(
            key=f"preference_step:{session_id}",
            value=step,
            ex=86400  # 24시간 TTL (더 긴 시간)
        )
        logger.info(f"Preference step saved: {step}")
    except Exception as e:
        logger.error(f"Failed to save preference step: {e}")
        # Redis 실패해도 계속 진행 (DB에서 복구 가능)

def _get_greeting_message() -> str:
    """사용자 인사 메시지 생성"""
    return (
        f"안녕하세요! 🎉 **AI 방탈출 월드**에 오신 것을 환영합니다!\n\n"
        "저는 당신에게 딱 맞는 방탈출을 추천해드리는 AI입니다!\n\n"
        "**방탈출은 해보신 적 있나요?**\n"
        "경험에 따라 맞춤형 추천을 해드릴게요! 😊"
    )
                
async def _save_preference_answer(user_id: int, step: str, answer: str, user_prefs: Dict):
    """사용자 답변을 선호도에 저장 (LLM 기반 자연어 처리)"""
    try:
        step_info = PREFERENCE_STEPS.get(step, {})
        field = step_info.get("field")
        
        if not field:
            logger.warning(f"Unknown step: {step}")
            return
        
        # LLM 기반 답변 분석 및 저장
        if step == "experience_check":
            analyzed_answer = await analyze_experience_answer(answer)
            if analyzed_answer == "experienced":
                user_prefs[field] = "방소년"  # 기본값
            else:
                user_prefs[field] = "방생아"
                
        elif step == "experience_count":
            analyzed_count = await analyze_experience_count(answer)
            user_prefs[field] = analyzed_count["count"]
            user_prefs['experience_level'] = analyzed_count["level"]
            
        elif step == "difficulty_check":
            difficulty = await analyze_difficulty_answer(answer)
            user_prefs[field] = difficulty
            
        elif step == "activity_level_check":
            activity_level = await analyze_activity_answer(answer)
            user_prefs[field] = activity_level
            
        elif step == "group_size_check":
            group_size = await analyze_group_size_answer(answer)
            user_prefs[field] = group_size
            
        elif step == "region_check":
            region = await analyze_region_answer(answer)
            user_prefs[field] = [region]
            
        elif step == "themes_check":
            theme = await analyze_theme_answer(answer)
            user_prefs[field] = [theme]
        
        # 선호도 업데이트
        await upsert_user_preferences(user_id, user_prefs)
        logger.info(f"Preference saved successfully: {step} = {user_prefs.get(field)}")
            
    except Exception as e:
        logger.error(f"Failed to save preference answer: {e}", step=step, answer=answer)
        # DB 저장 실패 시에도 진행 단계는 유지 (재시도 가능)
        raise CustomError("PREFERENCE_SAVE_FAILED", f"선호도 저장에 실패했습니다: {str(e)}")

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
        "created_at": now_korea_iso()
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

