"""LLM 및 임베딩 서비스 (공통 기능)"""

from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from .config import settings
from .logger import logger


class LLM:
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
    
    async def generate_response(self, conversation_history, user_level: str, user_prefs: dict) -> str:
        """LLM을 사용한 응답 생성"""
        try:
            # 대화 기록을 문자열로 변환
            history_text = ""
            for msg in conversation_history:
                role = "사용자" if msg.role == "user" else "AI"
                history_text += f"{role}: {msg.content}\n"
            
            # 사용자 선호사항을 문자열로 변환
            prefs_text = ""
            if user_prefs:
                for key, value in user_prefs.items():
                    if value:
                        prefs_text += f"- {key}: {value}\n"
            
            # LLM 호출
            response = await self.chain.ainvoke({
                "conversation_history": history_text,
                "user_message": conversation_history[-1].content if conversation_history else "",
                "user_level": user_level,
                "user_preferences": prefs_text
            })
            
            return response.content
            
        except Exception as e:
            logger.error(f"LLM response generation error: {e}")
            return "죄송합니다. 응답 생성 중 오류가 발생했습니다."
    
    async def create_embedding(self, text: str) -> list:
        """텍스트를 벡터 임베딩으로 변환"""
        try:
            embedding = await self.embeddings.aembed_query(text)
            return embedding
        except Exception as e:
            from .logger import logger
            logger.error(f"Embedding creation error: {e}")
            return []


# 전역 LLM 인스턴스
llm = LLM()
