# Escape Room AI Chatbot

**🚧 개발 중인 프로젝트** - AI 기반 방탈출 추천 챗봇 서비스입니다.

## 🚀 현재 구현된 기능

- **기본 FastAPI 구조** ✅
- **사용자 인증 시스템** ✅ (회원가입, 로그인)
- **PostgreSQL + Redis 연결** ✅
- **기본 챗봇 API 구조** ✅ (실제 추천 로직 미구현, 임베딩 변환 로직 구현)

## 개발 예정 기능

- **AI 챗봇**: 랭체인을 사용한 자연어 대화
- **방탈출 추천**: 사용자 선호사항 기반 맞춤형 추천
- **벡터 검색**: pgvector를 활용한 유사도 기반 검색
- **사용자 프로필**: 대화를 통한 사용자 선호사항 자동 추출

## 🛠️ 현재 기술 스택

- **Backend**: FastAPI, Python 3.11+
- **Database**: PostgreSQL 15 + pgvector
- **Cache**: Redis
- **Container**: Docker & Docker Compose
- **Authentication**: JWT, bcrypt

## 📁 현재 프로젝트 구조

```
escape-room-with-ai/
├── app/
│ ├── api/ # FastAPI 라우터 (auth, chat)
│ ├── core/ # 핵심 설정 및 데이터베이스 연결
│ ├── models/ # Pydantic 모델 (user, escape_room, questionnaire)
│ ├── services/ # 비즈니스 로직 (user_service만 구현)
│ └── main.py # FastAPI 앱
├── docker-compose.yml # Docker Compose 설정
├── Dockerfile # FastAPI 컨테이너
├── init.sql # 데이터베이스 초기화
└── requirements.txt # Python 의존성
```

## 🔍 현재 API 엔드포인트

### 인증
- `POST /auth/register` - 회원가입 ✅
- `POST /auth/login` - 로그인 ✅

### 채팅 (구조만 구현)
- `POST /chat/` - 챗봇과 대화 (기본 구조만)

## 🚧 개발 상태

- **완료**: 기본 구조, 인증 시스템, DB 연결
- **진행 중**: 챗봇 추천 로직
- **예정**: 벡터 검색, AI 응답 생성

