"""LLM 및 임베딩 서비스 (공통 기능)"""

from typing import List

from langchain.schema import BaseMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .config import settings
from .logger import logger


class LLMService:
    """LLM 서비스 레이어"""
    
    def __init__(self, provider: str = "openai"):
        self.provider = provider
        self._setup_llm()
        self._setup_embeddings()
    
    def _setup_llm(self):
        """LLM 설정"""
        if self.provider == "openai":
            self.llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.7,
                openai_api_key=settings.OPENAI_API_KEY
            )
        # NOTE: 확장 가능성과 유지보수 고려
        # elif self.provider == "anthropic":
        #     self.llm = ChatAnthropic(...)
        # elif self.provider == "google":
        #     self.llm = ChatGoogleGenerativeAI(...)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
    
    def _setup_embeddings(self):
        """임베딩 설정 (확장 가능)"""
        if self.provider == "openai":
            self.embeddings = OpenAIEmbeddings(openai_api_key=settings.OPENAI_API_KEY)
        # elif self.provider == "cohere":
        #     self.embeddings = CohereEmbeddings(...)
        else:
            raise ValueError(f"Unsupported embedding provider: {self.provider}")
    
    async def _generate_response(self, prompt: str) -> str:
        """단순 텍스트 생성 (내부용)"""
        try:
            response = await self.llm.ainvoke(prompt)
            return response.content
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            raise
    
    async def generate_with_messages(self, messages: List[BaseMessage]) -> str:
        """메시지 리스트로 생성 (LangChain 표준)"""
        try:
            response = await self.llm.agenerate([messages])
            return response.generations[0][0].text
        except Exception as e:
            logger.error(f"LLM generation with messages error: {e}")
            raise
    
    async def generate_with_messages_and_usage(self, messages: List[BaseMessage]) -> tuple[str, dict]:
        """메시지 리스트로 생성 + 토큰 사용량 반환"""
        try:
            response = await self.llm.agenerate([messages])
            text = response.generations[0][0].text
            usage = response.llm_output.get('token_usage', {}) if response.llm_output else {}
            return text, usage
        except Exception as e:
            logger.error(f"LLM generation with usage error: {e}")
            raise
    
    async def create_embedding(self, text: str) -> List[float]:
        """임베딩 생성"""
        try:
            return await self.embeddings.aembed_query(text)
        except Exception as e:
            logger.error(f"Embedding creation error: {e}")
            raise
    
    async def generate_chat_response(
        self, 
        conversation_history, 
        user_level: str = "방생아",
        user_preferences: dict = None
    ) -> str:
        """일반 대화용 응답 생성"""
        try:
            # 대화 기록을 문자열로 변환 (최근 3개만)
            history_text = ""
            recent_messages = conversation_history[-3:] if len(conversation_history) > 3 else conversation_history
            
            for msg in recent_messages:
                role = "사용자" if msg.role == "user" else "AI"
                history_text += f"{role}: {msg.content}\n"
            
            # 사용자 선호사항을 문자열로 변환
            prefs_text = ""
            if user_preferences:
                prefs_list = []
                for key, value in user_preferences.items():
                    if value:
                        prefs_list.append(f"{key}: {value}")
                prefs_text = ", ".join(prefs_list) if prefs_list else "없음"
            else:
                prefs_text = "없음"
            
            # 일반 대화용 프롬프트
            chat_prompt = f"""당신은 **친근한 AI 챗봇이면서 동시에 방탈출 전문 매니저**입니다.

## 🎯 당신의 역할
1. **일반 챗봇**: 사용자와 자연스러운 대화를 나누며 모든 주제에 대해 친근하게 응답
2. **방탈출 전문가**: 전국 방탈출 정보를 바탕으로 맞춤형 추천과 조언 제공

## 👤 사용자 정보
- 경험 등급: {user_level}
- 선호사항: {prefs_text}

## 💬 대화 기록 (참고용)
{history_text}

## 🎭 응답 가이드라인
- **친근하고 자연스러운 톤**으로 대화
- 이모지 적절히 사용 (😊🎯💡🔥)
- **오늘의 대화 내용에만 집중**하여 응답
- 이전 대화는 **컨텍스트로만 참고** (직접 언급하지 않음)
- **현재 메시지의 주제에만 집중**

응답해주세요:
"""
            
            return await self._generate_response(chat_prompt)
            
        except Exception as e:
            logger.error(f"Chat response generation error: {e}")
            return "죄송합니다. 응답 생성 중 오류가 발생했습니다."
    
    
# 전역 LLM 서비스 인스턴스
llm_service = LLMService()

# 하위 호환성을 위한 별칭
llm = llm_service
