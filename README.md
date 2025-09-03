# Escape Room AI Chatbot

AI 기반 방탈출 추천 챗봇 서비스입니다.

## 🚀 주요 기능

- **사용자 인증**: JWT + Redis 이중 검증
- **선호도 파악**: 단계별 질문을 통한 사용자 프로필 생성
- **AI 챗봇**: OpenAI GPT 기반 자연어 대화
- **방탈출 추천**: 하이브리드 검색 (필터 + 벡터 유사도)
- **세션 관리**: Redis 캐시 + PostgreSQL 백업

## 🏗️ 시스템 구조

```
app/
├── api/           # FastAPI 엔드포인트
│   ├── auth.py    # 인증 (회원가입, 로그인)
│   └── chat.py    # 챗봇 대화
├── services/      # 비즈니스 로직
│   ├── chat_service.py      # 챗봇 핵심 로직
│   ├── user_service.py      # 사용자 관리
│   ├── nlp_service.py       # 의도 분석
│   └── recommendation_service.py  # 추천 시스템
├── repositories/  # 데이터 접근
├── models/        # Pydantic 모델
└── utils/         # 유틸리티 (인증, 지역별(한국) 시간)
```

## 🔄 핵심 플로우

### **1. 사용자 인증**
1. 회원가입/로그인 → JWT 토큰 발급
2. 토큰을 Redis에 저장 (이중 검증)
3. API 요청 시 토큰 검증

### **2. 챗봇 대화**
1. **선호도 파악**: 신규 사용자 → 단계별 질문
2. **일반 대화**: 기존 사용자 → 방탈출 추천
3. **의도 분석**: LLM 기반 사용자 의도 파악
4. **추천 생성**: 하이브리드 검색 (필터 + 벡터)

## 🛠️ 기술 스택

- **Backend**: FastAPI, Python 3.13
- **Database**: PostgreSQL + pgvector (벡터 검색)
- **Cache**: Redis (세션 관리)
- **AI**: OpenAI GPT, LangChain
- **Authentication**: JWT + bcrypt

## 📋 API 엔드포인트

### **인증**
- `POST /auth/register` - 회원가입
- `POST /auth/login` - 로그인
- `GET /auth/me` - 현재 사용자 정보

### **채팅**
- `POST /chat/` - 챗봇과 대화
- `GET /health` - 헬스체크

## 🔄 API 동작 흐름

```mermaid
sequenceDiagram
    participant C as Client
    participant A as Auth API
    participant U as User Service
    participant Ch as Chat API
    participant CS as Chat Service
    participant AI as OpenAI

    C->>A: POST /auth/login
    A->>U: authenticate_user()
    U->>A: JWT Token
    A->>C: Token Response

    C->>Ch: POST /chat/ (with JWT)
    Ch->>U: verify_token()
    U->>Ch: User Info
    Ch->>CS: chat_with_user()
    CS->>AI: LLM Request
    AI->>CS: AI Response
    CS->>Ch: Chat Response
    Ch->>C: Final Response
```

## 🛡️ 예외 처리 구조

### **계층별 예외 처리 역할**

| 계층 | 역할 | 예외 처리 방식 |
|------|------|----------------|
| **API 계층** | 진입점만 제공 | 비즈니스 로직을 Service로 위임 |
| **Service 계층** | 비즈니스 로직 + 예외 처리 | CustomError/HTTPException 전파, Exception → CustomError 변환 |
| **Repository 계층** | DB 접근만 | DB 에러를 상위로 전파 |
| **Global Handler** | 최종 예외 처리 | 모든 예외를 HTTP 응답으로 변환 |

### **예외 처리 흐름**

```mermaid
graph TD
    A[API 엔드포인트] --> B[Service 계층]
    B --> C[Repository 계층]
    C --> D[DB 에러]
    
    B --> E{예외 발생}
    E -->|CustomError| F[Global Handler]
    E -->|HTTPException| F
    E -->|Exception| G[CustomError로 변환]
    G --> F
    
    F --> H{예외 타입}
    H -->|HTTPException| I[HTTP 응답]
    H -->|CustomError| J[구조화된 에러 응답]
    H -->|Exception| K[기본 에러 응답]
    
    style A fill:#e1f5fe
    style B fill:#f3e5f5
    style C fill:#e8f5e8
    style F fill:#fff3e0
```

### **예외 타입별 처리**

#### **1. CustomError (애플리케이션 커스텀 예외)**
```python
# Service 계층에서 발생
raise CustomError("VALIDATION_ERROR", "메시지를 입력해주세요.")

# Global Handler에서 처리
# → HTTP 422, {"status": "fail", "error_code": "200003", "message": "메시지를 입력해주세요."}
```

#### **2. HTTPException (FastAPI 기본 예외)**
```python
# Service 계층에서 발생
raise HTTPException(status_code=401, detail="인증이 필요합니다.")

# Global Handler에서 처리
# → HTTP 401, "인증이 필요합니다."
```

#### **3. Exception (예상치 못한 에러)**
```python
# Service 계층에서 발생
try:
    # 비즈니스 로직
    pass
except Exception as e:
    # CustomError로 변환
    raise CustomError("CHATBOT_ERROR", "챗봇 처리 중 오류가 발생했습니다.")

# Global Handler에서 처리
# → HTTP 500, {"status": "fail", "error_code": "202001", "message": "챗봇 처리 중 오류가 발생했습니다."}
```

### **에러 코드 체계**

| 에러 코드 | 카테고리 | 설명 |
|-----------|----------|------|
| **200xxx** | 공통 에러 | 시스템 전반적인 에러 |
| **201xxx** | 인증 에러 | 사용자 인증 관련 에러 |
| **202xxx** | 채팅 에러 | 챗봇 처리 관련 에러 |
| **203xxx** | 방탈출 에러 | 방탈출 데이터 관련 에러 |
| **204xxx** | Rate Limiting | 요청 제한 관련 에러 |

### **예외 처리 장점**

- **명확한 역할 분리**: 각 계층의 예외 처리 역할이 명확
- **일관된 에러 응답**: 모든 에러가 적절한 형태로 응답
- **중앙화된 처리**: Global Handler에서 모든 예외를 최종 처리
- **유연성**: CustomError와 HTTPException 모두 지원
- **안전성**: 예상치 못한 에러도 적절히 처리

<!-- ## 🚀 빠른 시작
- [API docs]() 로 접속해서 테스트 할 수 있습니다. 
- 또는 부하테스트를 원하시면 [부하테스트 링크]()로 접속하세요. -->
