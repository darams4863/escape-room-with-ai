"""LLM 및 임베딩 서비스 (공통 기능)"""

import time
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from .config import settings
from .logger import logger
from .monitor import track_api_call, track_performance


class LLM:
    """LLM 및 임베딩 서비스 (상태 유지 필요)"""
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            openai_api_key=settings.OPENAI_API_KEY
        )
        self.embeddings = OpenAIEmbeddings(openai_api_key=settings.OPENAI_API_KEY)
        
        # 경험 등급별 프롬프트 템플릿
        self.chat_prompt = PromptTemplate(
            input_variables=["conversation_history", "user_message", "user_level", "user_preferences"],
            template="""
당신은 **친근한 AI 챗봇이면서 동시에 방탈출 전문 매니저**입니다.

## 🎯 당신의 역할
1. **일반 챗봇**: 사용자와 자연스러운 대화를 나누며 모든 주제에 대해 친근하게 응답
2. **방탈출 전문가**: 전국 방탈출 정보를 바탕으로 맞춤형 추천과 조언 제공

## 🗺️ 보유 정보
- **전국 방탈출 데이터베이스**: 서울/인천/경기/충남/충북/제주도 등 전국 방탈출 정보
- **다양한 테마**: 추리, 공포, 로맨스, 판타지, SF, 역사, 액션 등 모든 장르
- **상세 정보**: 난이도, 가격, 인원수, 지역, 운영시간, 예약 방법, 활동성 등

## 👤 사용자 정보
- 경험 등급: {user_level}
- 선호사항: {user_preferences}

## 💬 대화 기록
{conversation_history}

## 📝 사용자 메시지
{user_message}

## 🎭 응답 가이드라인

### 1. 대화 스타일
- **친근하고 자연스러운 톤**으로 대화
- 이모지 적절히 사용 (😊🎯💡🔥)
- 사용자의 감정에 공감하고 격려

### 2. 방탈출 관련 질문 시
- 경험 등급에 맞는 톤으로 응답:
  - **방생아/방린이**: 친절한 가이드 톤 "처음이시라면 이 방이 딱 좋아요!"
  - **방소년/방어른**: 동료 게이머 톤 "이 정도는 가볍게 즐기실 수 있을 거예요"
  - **방신/방장로**: 존중 + 도전 톤 "이건 고인물도 혀를 내두른다는 방이에요"

### 3. 일반 대화 시
- 방탈출과 관련 없는 주제도 자연스럽게 대화
- 필요시 방탈출로 자연스럽게 연결
- 사용자의 관심사에 맞춰 대화

### 4. 추천 시 고려사항
- 사용자 선호사항 (지역, 테마, 난이도, 가격, 인원수)
- 경험 등급에 맞는 난이도
- 계절, 시간대, 특별한 상황 고려

## 📋 응답 형식
- **자연스러운 대화**: 사용자와의 친근한 대화
- **구체적 정보**: 필요시 방탈출 상세 정보 제공
- **다음 단계 제안**: 관련 질문이나 추천 유도

응답해주세요:
"""
        )
        
        # LLMChain 대신 최신 방식 사용
        self.chain = self.chat_prompt | self.llm
    
    @track_performance("llm_generate_response")
    async def generate_response(
        self, 
        conversation_history, 
        user_level: str = "방생아",
        user_preferences: dict = None,
        custom_prompt: str = None
    ) -> str:
        """LLM을 사용한 응답 생성"""
        try:
            start_time = time.time()
            
            # 커스텀 프롬프트가 있으면 직접 사용
            if custom_prompt:
                response = await self.llm.ainvoke(custom_prompt)
            else:
                # 기본 프롬프트 사용
                # 대화 기록을 문자열로 변환
                history_text = ""
                for msg in conversation_history:
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
                
                # LLM 호출
                response = await self.chain.ainvoke({
                    "conversation_history": history_text,
                    "user_message": conversation_history[-1].content if conversation_history else "",
                    "user_level": user_level,
                    "user_preferences": prefs_text
                })
            
            response_time = time.time() - start_time
            
            # 실제 토큰 사용량 기반 비용 계산
            llm_output = response.response_metadata
            prompt_tokens = llm_output.get('token_usage', {}).get('prompt_tokens', 0)
            completion_tokens = llm_output.get('token_usage', {}).get('completion_tokens', 0)
            
            # GPT-4o-mini 가격 (2025년 9월 20일 기준)
            input_cost = (prompt_tokens / 1000000) * 0.15  # $0.15 per 1M tokens
            output_cost = (completion_tokens / 1000000) * 0.60  # $0.60 per 1M tokens
            total_cost = input_cost + output_cost
            
            # 한국 원화 환율 계산 (1 USD = 1500 KRW)
            total_cost_krw = total_cost * 1500
            logger.info(f"LLM 비용: ${total_cost:.6f} (₩{total_cost_krw:.2f}) - Input: {prompt_tokens} tokens, Output: {completion_tokens} tokens")
            
            # API 호출 메트릭 수집 (한 번만)
            track_api_call(
                service="openai",
                endpoint="generate_response",
                status_code=200,
                duration_seconds=response_time,
                model="gpt-4o-mini",
                cost_usd=total_cost
            )
            
            return response.content
            
        except Exception as e:
            logger.error(f"LLM response generation error: {e}")
            # API 호출 실패 메트릭 수집
            track_api_call(model="gpt-4o-mini", success=False)
            return "죄송합니다. 응답 생성 중 오류가 발생했습니다."
    
    @track_performance("llm_create_embedding")
    async def create_embedding(self, text: str) -> list:
        """텍스트를 벡터 임베딩으로 변환"""
        try:
            start_time = time.time()
            response = await self.embeddings.aembed_query(text)
            response_time = time.time() - start_time
            
            # 실제 토큰 사용량 기반 비용 계산
            # text-embedding-ada-002: $0.0001 per 1K tokens (2025년 9월 20일 기준)
            # 임베딩은 response가 list이므로 토큰 수를 추정
            estimated_tokens = len(text.split()) * 1.3  # 대략적인 토큰 수 추정
            cost_usd = (estimated_tokens / 1000) * 0.0001
            
            # 한국 원화 환율 계산 (1 USD = 1500 KRW)
            total_cost_krw = cost_usd * 1500
            logger.info(f"임베딩 생성 비용: ${cost_usd:.6f} (₩{total_cost_krw:.2f}) - 추정 토큰: {estimated_tokens}")
            
            # API 호출 메트릭 수집
            track_api_call(
                service="openai",
                endpoint="create_embedding",
                status_code=200,
                duration_seconds=response_time,
                model="text-embedding-ada-002",
                cost_usd=cost_usd
            )
            return response
        except Exception as e:
            logger.error(f"Embedding creation error: {e}")
            return []


llm = LLM()
