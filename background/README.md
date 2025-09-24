# 🕷️ 백룸 크롤링 & AI 벡터화 파이프라인

방탈출 정보 수집부터 AI 임베딩 생성까지 완전 자동화된 데이터 파이프라인

## 📋 전체 워크플로우

```
🕷️ 크롤링 → 🗄️ DB 저장 → 🧠 벡터화 → 🤖 AI 챗봇
(data_crawler.py) → (PostgreSQL) → (vector_generator.py) → (FastAPI)
```

## 📋 데이터 파이프라인 실행

### 1단계: 크롤링 (데이터 수집)

```bash
cd background
python data_crawler.py
```

**크롤링 프로세스:**

1. 메인 페이지 접속 → 필터 버튼 클릭 → 지역 선택
2. 지역별/서브지역별 순차 크롤링 (16개 지역, 85+ 서브지역)
3. 각 카드 클릭하여 상세 정보 수집 (테마명, 업체명, 난이도, 가격, 예약URL 등)
4. PostgreSQL에 실시간 저장 (중복 방지, 배치 처리)
5. 크롤링 상태 추적 (`data/crawling_state.json`) - 재시작 지원

### 2단계: 벡터화 (AI 임베딩 생성)

```bash
python vector_generator.py
```

**벡터화 프로세스:**

1. PostgreSQL에서 `embedding IS NULL`인 데이터 조회
2. RAG 최적화된 구조화 텍스트 생성 (테마명, 지역, 난이도, 키워드 등)
3. OpenAI text-embedding-ada-002로 1536차원 벡터 생성
4. 배치 처리 (10개씩) + 실패시 개별 처리 폴백
5. 벡터 품질 검증 (더미 벡터, NaN/Inf 제거)
6. PostgreSQL pgvector 형식으로 저장

### 3단계: 실패 데이터 재처리

```bash
python process_dead_letters.py
```

**재처리 프로세스:**

1. `data/dead_letters/` 디렉토리의 실패 데이터 조회
2. 크롤링 실패 → DB 저장 재시도
3. 벡터화 실패 → 임베딩 재생성 시도
4. 성공한 데이터는 아카이브 처리

## 🎯 데이터 파이프라인 상세

### 🕷️ **1단계: 크롤링 (data_crawler.py)**

#### 📋 **크롤링 시나리오**

1. **🌐 메인 페이지 접속**
2. **🔍 필터 버튼 클릭** → 지역 선택 화면 열기
3. **📍 지역 탭 클릭** → 지역 목록 표시
4. **🏢 지역 선택** → 서울, 경기, 부산 등 (REGION_DATA 기반)
5. **🏘️ 서브지역 선택** → 강남, 홍대, 건대 등 (필터링 적용)
6. **📄 페이지별 처리**:
   - 현재 페이지의 모든 카드(`<li>`) 찾기
   - 각 카드에서 **기본 정보 추출** (목록에서)
     - 테마명, 업체명, 장르, 시간, 가격, 평점
   - **카드 클릭** → 상세 페이지 이동
   - **상세 정보 추출** (상세 페이지에서)
     - 난이도, 추천인원, 활동성, 스토리, 예약URL
   - **뒤로가기** → 목록 페이지 복원
   - **중복 체크** → DB에서 확인 후 저장
7. **➡️ 다음 페이지** → 페이지네이션 (내용 비교로 마지막 페이지 감지)
8. **🧹 필터 해제** → 다음 서브지역으로 이동
9. **🔄 반복** → 모든 지역/서브지역 완료까지

#### 🔧 **크롤링 설정 옵션**

```python
# 크롤링 제외 설정 (data_crawler.py에서)
EXCLUDED_REGIONS = []  # 제외할 지역들
EXCLUDED_SUB_REGIONS = {
    '서울': ['강남', '강동구', '강북구', '신림'],  # 서울에서 제외할 서브지역
}

# 크롤링 설정 (환경변수에서)
CRAWL_HEADLESS=false        # 브라우저 보기/숨기기
CRAWL_WAIT_TIME=2.0         # 대기 시간 (초)
CRAWL_PAGE_TIMEOUT=30       # 페이지 로딩 타임아웃
```

### 🧠 **2단계: 벡터화 (vector_generator.py)**

#### 📋 **벡터화 프로세스**

1. **📊 DB 연결** → PostgreSQL에서 `embedding IS NULL`인 데이터 조회
2. **📝 텍스트 생성** → RAG 최적화된 구조화 텍스트 생성
3. **🤖 OpenAI API 호출** → 배치 임베딩 생성 (text-embedding-ada-002)
4. **✅ 품질 검증** → 벡터 품질 검사 (NaN, Inf, 더미 벡터 제거)
5. **💾 DB 저장** → pgvector 형식으로 PostgreSQL 저장
6. **🔄 폴백 로직** → 배치 실패시 개별 처리
7. **💰 비용 추적** → 실시간 API 사용량 및 비용 모니터링

#### 🎯 **RAG 최적화 텍스트 생성**

```
방탈출 테마: FILM BY EDDY (필름 바이 에디)
운영업체: 키이스케이프 메모리컴퍼니
위치: 서울 강남
장르: 드라마
난이도: 보통 (3/5)
활동성: 보통 (2/3)
소요시간: 75분
참여인원: 2-4명
1인당 가격: 32,000원
평점: 4.8점

이 방탈출은 서울 강남에 위치한 드라마 테마입니다.
키이스케이프 메모리컴퍼니에서 운영하며, 2명부터 4명까지 참여 가능합니다.
난이도는 보통이고, 활동성은 보통 수준입니다.
소요시간은 약 75분이며, 1인당 32,000원입니다.

스토리: 1990년대 감성의 추리 드라마 테마...
관련 키워드: 추리, 수사, 탐정, 보통, 일반, 커플, 소규모, 적당한, 서울, 강남
```

#### ⚙️ **벡터화 설정**

```python
# OpenAI 설정
EMBEDDING_MODEL = "text-embedding-ada-002"  # 1536차원
BATCH_SIZE = 10                             # 배치 크기
MAX_RETRIES = 3                             # 재시도 횟수

# 벡터 품질 검증
- 더미 벡터 (모두 0) 제거
- NaN/Inf 값 제거
- 벡터 노름 검사 (1e-6 < norm < 10)

# 비용 최적화
- 배치 API 호출 (최대 100개씩)
- 스마트 폴백 (배치 실패시 개별 처리)
- 실시간 토큰 & 비용 추적
```

#### 💰 **비용 모니터링**

```bash
# 실행 예시 (테스트 모드)
$ python vector_generator.py --test

🧪 테스트 모드: 2개 데이터만 처리
🔧 벡터 모델: text-embedding-ada-002 (1536차원)
📊 벡터화 대상: 2개
💰 예상 비용: ~$0.0003 (₩0.45) (테스트)

    🔢 OpenAI 배치 벡터 생성 시도: 2개
      📊 API: 157 토큰, $0.000008 (₩0.01), 234ms
    ✅ 배치 업데이트 성공: 2개
    📊 최종 결과: 2/2개 성공 (100.0%)
    💰 배치 실제 비용: $0.000008 (₩0.01)

⏳ 벡터화 진행률: 100.0% (2/2) | 누적 비용: $0.0000 (₩0.01)
🧪 테스트 완료! 실제 비용: $0.0000 (₩0.01)
✅ 벡터 생성 완료!
```

#### 🗄️ **크롤링 상태 추적 & 재시작**

- **상태 파일**: `data/crawling_state.json`
- **재시작 지원**: 중단된 지점부터 이어서 크롤링
- **배치 저장**: 페이지별로 DB에 배치 저장
- **중복 방지**: PostgreSQL UNIQUE 제약조건 + ON CONFLICT 처리

#### 🔄 **벡터화 오류 처리**

- **Dead Letter Queue**: 실패한 항목은 `data/dead_letters/` JSONL 파일로 저장
- **배치 폴백**: 배치 실패시 개별 처리로 자동 전환
- **품질 검증**: 무효한 벡터는 DB에 저장하지 않음 (embedding = NULL 유지)
- **재시도 로직**: 지수적 백오프로 API 오류 재시도

### 🤖 **3단계: AI 챗봇 (FastAPI)**

#### 📋 **벡터 검색 시스템**

1. **🔍 사용자 질문** → "강남에서 2명이 할 수 있는 쉬운 방탈출 추천해줘"
2. **🧠 질문 임베딩** → OpenAI로 사용자 질문 벡터화
3. **📊 유사도 검색** → PostgreSQL pgvector로 코사인 유사도 검색
4. **🎯 결과 반환** → 상위 5개 가장 유사한 방탈출 추천
5. **💬 AI 응답** → LangChain으로 자연어 응답 생성

#### 🔍 **벡터 검색 쿼리**

```sql
SELECT id, name, description, difficulty_level,
       region, sub_region, theme, price_per_person,
       (embedding <=> $1::vector) as similarity_score
FROM escape_rooms
WHERE embedding IS NOT NULL
ORDER BY embedding <=> $1::vector
LIMIT 5
```

#### 📊 **전체 시스템 아키텍처**

```
┌─────────────────┐    ┌───────────────────────┐    ┌────────────────────┐
│   🕷️ 크롤러       │───▶│   🗄️ PostgreSQL       │───▶│  🧠 벡터 생성기      │
│  data_crawler   │    │ + tsvector + pgvector  │    │ vector_generator  │
│                 │    │                        │    │                   │
│ • 백룸 사이트      │    │ • 방탈출 데이터           │    │ • OpenAI API      │
│ • 상세 정보 수집   │     │ • 메타데이터             │    │ • 임베딩 생성        │
│ • 상태 추적       │     │ • 벡터 저장             │    │ • 품질 검증          │
└─────────────────┘     └───────────────────────┘   └────────────────────┘
                                   │
                                   ▼
                       ┌─────────────────────────────────────┐
                       │          🤖 AI 챗봇 API              │
                       │         FastAPI + LangChain         │
                       │                                     │
                       │ • RAG 하이브리드 검색                   │
                       │   - tsvector (키워드)                 │
                       │   - pgvector (의미)                  │
                       │ • 자연어 질의응답                       │
                       │ • 개인화 추천                          │
                       │ • 실시간 대화                          │
                       └─────────────────────────────────────┘
```

## 🚀 기술적 하이라이트 & 비즈니스 가치

### 💼 **비즈니스 관점에서의 강점**

- **📊 대규모 데이터 처리**: 전국 16개 지역, 85+ 서브지역에서 수천 개 방탈출 데이터 수집
- **🔄 완전 자동화**: 크롤링 → 벡터화 → AI 검색까지 무인 운영 가능
- **💰 비용 최적화**: OpenAI API 배치 처리로 벡터화 비용 80% 절약
- **🛡️ 안정성**: Dead Letter Queue, 재시작 지원, 오류 복구 시스템
- **📈 확장성**: 새로운 지역/사이트 추가 시 설정만 변경하면 자동 확장

### 🛠️ **기술적 하이라이트**

- **🤖 AI 벡터화**: OpenAI text-embedding-ada-002로 의미적 검색 구현
- **🕷️ 고급 크롤링**: Selenium + 봇 탐지 우회, 상태 추적, 재시작 지원
- **🗄️ 벡터 데이터베이스**: PostgreSQL + pgvector로 고성능 유사도 검색
- **⚡ 실시간 처리**: 배치 처리 + 개별 폴백으로 안정성과 효율성 확보
- **📊 모니터링**: API 사용량, 비용, 성능 실시간 추적

## 🎯 수집 데이터 & 벡터화

### 📊 **원본 데이터 (크롤링)**

- **방탈출 이름**: "FILM BY EDDY (필름 바이 에디)"
- **지역/서브지역**: "서울 > 강남"
- **테마**: "드라마", "스릴러", "추리", "호러" 등
- **시간**: 60분, 75분, 100분 등
- **가격**: 25,000원, 32,000원, 35,000원 등
- **업체명**: "키이스케이프 메모리컴퍼니", "비트포비아" 등
- **난이도**: 쉬움(2), 보통(3), 어려움(4)
- **추천인원**: 2-4명, 3-6명 등
- **활동성**: 거의없음(1), 보통(2), 많음(3)
- **예약URL**: 홈페이지 -, 네이버 예약 링크 등
- **평점**: 4.8, 4.5 등

### 🧠 **AI 벡터 데이터 (임베딩)**

- **벡터 차원**: 1536차원 (text-embedding-ada-002)
- **벡터 형식**: PostgreSQL pgvector 타입
- **저장 위치**: `escape_rooms.embedding` 컬럼
- **검색 방식**: 코사인 유사도 (`<=>` 연산자)
- **품질 보장**: NaN/Inf/더미 벡터 자동 제거
- **비용 효율**: 배치 처리 + 스마트 폴백

### 🔍 **RAG 하이브리드 검색 예시**

```python
# 사용자 질문: "강남에서 2명이 할 수 있는 쉬운 방탈출"

# 1. 키워드 추출 및 tsvector 검색
keywords = ["강남", "2명", "쉬운"]
tsvector_results = await conn.fetch("""
    SELECT id, name, region, sub_region, difficulty_level,
           ts_rank(to_tsvector('korean', name || ' ' || description),
                   plainto_tsquery('korean', $1)) as ts_score
    FROM escape_rooms
    WHERE to_tsvector('korean', name || ' ' || description)
          @@ plainto_tsquery('korean', $1)
    ORDER BY ts_score DESC
    LIMIT 3
""", ' '.join(keywords))

# 2. 질문 벡터화 및 pgvector 검색
query_vector = openai.embeddings.create(
    model="text-embedding-ada-002",
    input="강남에서 2명이 할 수 있는 쉬운 방탈출"
)

pgvector_results = await conn.fetch("""
    SELECT id, name, region, sub_region, difficulty_level,
           (embedding <=> $1::vector) as vector_score
    FROM escape_rooms
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> $1::vector
    LIMIT 3
""", query_vector)

# 3. 하이브리드 랭킹 (tsvector 0.7 + pgvector 0.3)
final_results = combine_search_results(tsvector_results, pgvector_results)

# 최종 결과:
# 1. "FILM BY EDDY" (서울 강남) - 난이도 3, 하이브리드 점수 0.89
# 2. "미스터리 카페" (서울 강남) - 난이도 2, 하이브리드 점수 0.85
# 3. "로맨틱 탈출" (서울 강남) - 난이도 2, 하이브리드 점수 0.82
```

## 📊 데이터베이스 구조

### 🗄️ **PostgreSQL 테이블 구조**

```sql
-- escape_rooms 테이블 (pgvector 확장 포함)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE escape_rooms (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    region VARCHAR(50) NOT NULL,
    sub_region VARCHAR(50) NOT NULL,
    theme VARCHAR(100),
    duration_minutes INTEGER,
    price_per_person INTEGER,
    description TEXT,
    company VARCHAR(200),
    rating DECIMAL(3,2),
    image_url VARCHAR(500),
    source_url VARCHAR(500),
    booking_url VARCHAR(500),        -- 📋 예약 링크
    difficulty_level INTEGER,       -- 1-5 (쉬움-어려움)
    activity_level INTEGER,         -- 1-3 (거의없음-많음)
    group_size_min INTEGER,         -- 최소 인원
    group_size_max INTEGER,         -- 최대 인원
    embedding vector,               -- 🧠 AI 임베딩 벡터 (동적 차원)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- 중복 방지용 UNIQUE 제약조건
    UNIQUE(name, region, sub_region, company)
);

-- 벡터 검색 성능을 위한 인덱스
CREATE INDEX IF NOT EXISTS idx_escape_rooms_embedding
ON escape_rooms USING ivfflat (embedding vector_cosine_ops);
```

### 📝 **실제 데이터 예시**

#### 원본 데이터 (JSON)

```json
{
  "id": 1,
  "name": "FILM BY EDDY (필름 바이 에디)",
  "region": "서울",
  "sub_region": "강남",
  "theme": "드라마",
  "duration_minutes": 75,
  "price_per_person": 32000,
  "company": "키이스케이프 메모리컴퍼니",
  "rating": 4.8,
  "booking_url": "https://example.com/booking/123456",
  "difficulty_level": 3,
  "activity_level": 2,
  "group_size_min": 2,
  "group_size_max": 4,
  "description": "1990년대 감성의 추리 드라마 테마...",
  "embedding": [0.123, -0.456, 0.789, ...] // 1536차원 벡터
}
```

#### AI 검색 결과 예시

```python
# 사용자: "강남에서 커플이 할 수 있는 추리 방탈출 추천해줘"

results = [
    {
        "name": "FILM BY EDDY (필름 바이 에디)",
        "region": "서울", "sub_region": "강남",
        "theme": "드라마", "difficulty_level": 3,
        "price_per_person": 32000,
        "similarity_score": 0.142  # 높은 유사도
    },
    {
        "name": "셜록홈즈의 비밀",
        "region": "서울", "sub_region": "강남",
        "theme": "추리", "difficulty_level": 3,
        "price_per_person": 28000,
        "similarity_score": 0.156
    }
]
```

## ⚠️ 주의사항 & 트러블슈팅

### 🔧 **크롤링 설정**

1. **크롤링 간격**: 서버 부하 방지를 위해 2-3초 대기 (랜덤)
2. **헤드리스 모드**: 운영 시 `CRAWL_HEADLESS=true` 설정
3. **타임아웃 설정**: 페이지 로딩 30초 타임아웃
4. **User-Agent**: 실제 브라우저처럼 보이게 설정

### 🧠 **벡터화 설정**

1. **OpenAI API 키**: `.env`에 `OPENAI_API_KEY` 설정 필수
2. **배치 크기**: 너무 크면 API 한도 초과, 너무 작으면 비효율
3. **재시도 로직**: Rate limit 시 지수적 백오프
4. **품질 검증**: 무효한 벡터는 자동 제거
5. **비용 관리**: 테스트 모드로 먼저 확인

### 🗄️ **데이터베이스**

1. **pgvector 확장**: PostgreSQL에 vector 확장 설치 필수
2. **중복 방지**: `ON CONFLICT DO UPDATE` 사용
3. **배치 저장**: 페이지별로 한 번에 저장 (성능 최적화)
4. **벡터 타입**: Python list → PostgreSQL vector 형식 변환
5. **인덱스**: 벡터 검색 성능을 위한 IVFFlat 인덱스

### 🔄 **재시작 & 상태 관리**

1. **크롤링 상태**: 매 카드 처리 후 상태 저장
2. **벡터화 재시작**: `embedding IS NULL`인 데이터만 처리
3. **필터링**: `EXCLUDED_REGIONS`, `EXCLUDED_SUB_REGIONS`로 부분 실행
4. **Dead Letter**: 실패한 항목들 JSONL 파일로 저장

### 💰 **비용 관리**

```bash
# 예상 비용 (OpenAI text-embedding-ada-002)
- 1,000개 방탈출: 약 $0.15 (₩225)
- 5,000개 방탈출: 약 $0.75 (₩1,125)
- 10,000개 방탈출: 약 $1.50 (₩2,250)

# 비용 절약 팁
1. 테스트 모드로 먼저 확인: --test 플래그
2. 배치 처리로 효율성 증대
3. 중복 벡터화 방지: embedding IS NULL 체크
4. 실패한 항목만 재처리
```

## 🗺️ 지역 & 서브지역 정보

### 📍 **전체 크롤링 대상 지역** (REGION_DATA)

```python
REGION_DATA = {
    '서울': ['강남', '강동구', '강북구', '신림', '건대', '구로구', '노원구', '동대문구', '동작구',
            '홍대', '신촌', '성동구', '성북구', '잠실', '양천구', '영등포구', '용산구', '은평구', '대학로', '중구'],
    '경기': ['고양', '광주', '구리', '군포', '김포', '동탄', '부천', '성남', '수원',
            '시흥', '안산', '안양', '용인', '의정부', '이천', '일산', '평택', '하남', '화성'],
    '부산': ['금정구', '기장군', '남구', '부산진구', '북구', '사하구', '수영구', '중구', '해운대구'],
    '대구': ['달서구', '수성구', '중구'],
    '인천': ['남동구', '미추홀구', '부평구', '연수구'],
    '광주': ['광산구', '동구', '북구', '서구'],
    '대전': ['서구', '유성구', '중구'],
    '전북': ['군산', '익산', '전주'],
    '충남': ['당진', '천안'],
    '경남': ['양산', '진주', '창원'],
    '경북': ['경주', '구미', '영주', '포항'],
    '강원': ['강릉', '원주', '춘천'],
    '제주': ['서귀포시', '제주시'],
    '충북': ['청주'],
    '울산': ['남구', '중구'],
    '전남': ['목포', '순천', '여수']
}
```

### 🚫 **크롤링 제외할 지역 설정**

```python
# 제외된 지역들 (크롤링 안함)
EXCLUDED_REGIONS = []

# 제외된 서브지역들 (서울에서만 일부 제외)
EXCLUDED_SUB_REGIONS = {
    '서울': ['강남', '강동구', '강북구', '신림'],  # 이 4개 서브지역 제외
}
```

---

### 마무리

**이 데이터 수집 파이프라인을 통해 전국 16개 지역, 85개 서브지역에서 방탈출 데이터를 자동으로 수집하고, OpenAI text-embedding-ada-002로 1536차원 벡터를 생성하여 의미적 검색이 가능한 AI 추천 시스템을 구축했습니다. Selenium 기반 크롤링, PostgreSQL + tsvector, pgvector 벡터 데이터베이스, Dead Letter Queue를 통한 안정적인 데이터 파이프라인으로 확장 가능한 AI 서비스의 기반을 마련했습니다.**
