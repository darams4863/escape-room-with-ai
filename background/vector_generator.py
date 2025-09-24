"""
크롤링된 데이터를 벡터화 (임베딩 생성)
사용법: 가상환경에서 python background/vector_generator.py
"""

import asyncio
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import asyncpg
from openai import AsyncOpenAI

# 프로젝트 루트를 Python path에 추가
sys.path.append(str(Path(__file__).parent.parent))

# 환경변수 로드
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

from app.core.config import settings

class VectorGenerator:
    """방탈출 데이터 벡터화 및 임베딩 생성"""
    
    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.embedding_model = getattr(settings, 'embedding_model', 'text-embedding-ada-002')
        self.embedding_dimension = None  # 동적으로 결정
        
    async def generate_vectors(self, test_mode: bool = False, test_limit: int = 2):
        """크롤러 이후 DB 데이터 벡터화 (단일 모드)"""
        if test_mode:
            print(f"🧪 테스트 모드: {test_limit}개 데이터만 처리")
        else:
            print(f"📥 DB 데이터 벡터화 시작...")
        await self._vectorize_db_data(test_mode, test_limit)
        print("✅ 벡터 생성 완료!")
    

    async def _vectorize_db_data(self, test_mode: bool = False, test_limit: int = 2):
        """DB의 기존 데이터를 벡터화 (크롤러 이후 실행)"""
        # 벡터 모델 차원 동적 감지
        await self._detect_vector_dimension()
        print(f"🔧 벡터 모델: {self.embedding_model} ({self.embedding_dimension}차원)")
        
        # PostgreSQL 연결
        conn = await asyncpg.connect(settings.database_url)
        
        try:
            # 벡터가 없는 데이터 조회 (테스트 모드 적용)
            limit_clause = f"LIMIT {test_limit}" if test_mode else ""
            rows = await conn.fetch(f"""
                SELECT id, name, description, theme, region, sub_region, company,
                       difficulty_level, activity_level, duration_minutes, 
                       price_per_person, group_size_min, group_size_max, rating
                FROM escape_rooms 
                WHERE embedding IS NULL
                ORDER BY id
                {limit_clause}
            """)
            
            if not rows:
                print("📋 벡터화가 필요한 데이터가 없습니다")
                return
                
            print(f"📊 벡터화 대상: {len(rows)}개")
            
            if test_mode:
                estimated_cost = len(rows) * 0.00015
                estimated_krw = estimated_cost * 1500
                print(f"💰 예상 비용: ~${estimated_cost:.4f} (₩{estimated_krw:.2f}) (테스트)")
            else:
                total_cost = len(rows) * 0.00015  # 대략적 추정
                total_krw = total_cost * 1500
                print(f"💰 예상 총 비용: ~${total_cost:.2f} (₩{total_krw:.0f})")
            
            # 배치 단위로 처리
            batch_size = 1 if test_mode else getattr(settings, 'crawl_batch_size', 10)
            total_cost_actual = 0.0
            
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                success_count, batch_cost = await self._process_db_batch(conn, batch, test_mode)
                total_cost_actual += batch_cost
                
                # 진행률 표시
                progress = min((i + batch_size) / len(rows) * 100, 100)
                krw_actual = total_cost_actual * 1500
                print(f"⏳ 벡터화 진행률: {progress:.1f}% ({i + len(batch)}/{len(rows)}) | 누적 비용: ${total_cost_actual:.4f} (₩{krw_actual:.2f})")
                
                # 테스트 모드에서는 즉시 중단
                if test_mode:
                    krw_test_final = total_cost_actual * 1500
                    print(f"🧪 테스트 완료! 실제 비용: ${total_cost_actual:.4f} (₩{krw_test_final:.2f})")
                    break
                
        finally:
            await conn.close()
    
    
    async def _process_db_batch(self, conn: asyncpg.Connection, batch, test_mode: bool = False):
        """DB 데이터 배치 벡터화 (스마트 폴백 전략)"""
        # 1. 모든 row의 설명 텍스트 생성
        descriptions = []
        row_data = []
        
        for row in batch:
            item_dict = dict(row)
            description = self._generate_description_from_dict(item_dict)
            descriptions.append(description)
            row_data.append({
                'id': row['id'],
                'name': row['name'],
                'description': description,
                'processed': False
            })
        
        print(f"    🔢 OpenAI 배치 벡터 생성 시도: {len(descriptions)}개")
        
        # 2. 배치 처리 시도 (실제 비용 추적)
        self._current_batch_cost = 0.0  # 배치 비용 초기화
        success_count = await self._try_batch_vectorization(conn, descriptions, row_data)
        
        # 3. 실패한 항목들 개별 처리
        failed_items = [item for item in row_data if not item['processed']]
        if failed_items:
            print(f"    🔄 실패한 {len(failed_items)}개 항목 개별 처리...")
            individual_success = await self._process_failed_items_individually(conn, failed_items)
            success_count += individual_success
        
        total_items = len(batch)
        print(f"    📊 최종 결과: {success_count}/{total_items}개 성공 ({success_count/total_items*100:.1f}%)")
        
        # 실제 API 비용 사용 (개별 호출에서 누적된 비용)
        actual_batch_cost = getattr(self, '_current_batch_cost', 0.0)
        
        if test_mode:
            krw_batch_cost = actual_batch_cost * 1500
            print(f"    💰 배치 실제 비용: ${actual_batch_cost:.6f} (₩{krw_batch_cost:.2f})")
        
        return success_count, actual_batch_cost
    
    async def _try_batch_vectorization(self, conn: asyncpg.Connection, descriptions: List[str], row_data: List[Dict]) -> int:
        """배치 벡터화 시도"""
        try:
            # OpenAI 배치 API 호출
            vectors = await self._generate_vectors_batch(descriptions)
            
            if len(vectors) != len(descriptions):
                print(f"    ⚠️ 벡터 수 불일치: {len(vectors)} vs {len(descriptions)} - 개별 처리로 전환")
                return 0
            
            # 유효한 벡터만 필터링 (더미 벡터 제외)
            valid_updates = []
            for i, (vector, row_info) in enumerate(zip(vectors, row_data)):
                # 더미 벡터 체크 (모두 0.0인 경우)
                if not all(v == 0.0 for v in vector):
                    valid_updates.append((vector, row_info['id']))
                    row_info['processed'] = True
                else:
                    print(f"      ⚠️ 더미 벡터 감지: {row_info['name']} (ID: {row_info['id']})")
            
            if not valid_updates:
                print(f"    ❌ 유효한 벡터가 없음 - 개별 처리 필요")
                return 0
            
            # PostgreSQL 배치 업데이트 (vector 타입으로 변환)
            # Python list를 PostgreSQL vector 형식으로 변환
            formatted_updates = []
            for vector, room_id in valid_updates:
                # list를 "[1,2,3]" 형식의 문자열로 변환
                vector_str = '[' + ','.join(map(str, vector)) + ']'
                formatted_updates.append((vector_str, room_id))
            
            await conn.executemany("""
                UPDATE escape_rooms 
                SET embedding = $1::vector 
                WHERE id = $2
            """, formatted_updates)
            
            print(f"    ✅ 배치 업데이트 성공: {len(valid_updates)}개")
            return len(valid_updates)
                
            except Exception as e:
            print(f"    ❌ 배치 처리 실패: {e}")
            return 0
    
    async def _process_failed_items_individually(self, conn: asyncpg.Connection, failed_items: List[Dict]) -> int:
        """실패한 항목들 개별 처리"""
        success_count = 0
        final_failures = []
        
        for item in failed_items:
            try:
                # 개별 벡터 생성
                vector = await self._generate_vector(item['description'])
                
                # None 체크 (API 최종 실패)
                if vector is None:
                    print(f"      ❌ {item['name']}: API 호출 최종 실패")
                    final_failures.append({
                        **item,
                        "failure_reason": "api_call_failed"
                    })
                    continue
                
                # 벡터 품질 검증 - 실패시 DB 업데이트 하지 않음
                if not self._validate_vector_quality(vector):
                    print(f"      ❌ {item['name']}: 유효하지 않은 벡터 (더미/NaN/Inf)")
                    final_failures.append({
                        **item,
                        "failure_reason": "invalid_vector_quality"
                    })
                    # 🎯 핵심: DB 업데이트 하지 않음 (embedding = NULL 유지)
                    continue
                
                # 유효한 벡터만 DB 업데이트 
                vector_str = '[' + ','.join(map(str, vector)) + ']'
                await conn.execute("""
                    UPDATE escape_rooms 
                    SET embedding = $1::vector 
                    WHERE id = $2
                """, vector_str, item['id'])
                
                print(f"      ✅ {item['name']} (ID: {item['id']})")
                success_count += 1
                
            except Exception as e:
                print(f"      ❌ {item['name']}: {e}")
                final_failures.append({
                    **item,
                    "failure_reason": str(e)
                })
                continue
        
        # 최종 실패한 항목들 Dead Letter 저장
        if final_failures:
            await self._save_failures_to_dead_letter(final_failures)
        
        return success_count
    
    async def _save_failures_to_dead_letter(self, failed_items: List[Dict]):
        """벡터화 실패 항목들을 Dead Letter Queue에 저장"""
        try:
            # DLQ 디렉토리 생성
            dlq_dir = Path("data/dead_letters")
            dlq_dir.mkdir(parents=True, exist_ok=True)
            
            # 날짜별 파일 생성
            date_str = datetime.now().strftime("%Y%m%d")
            dlq_file = dlq_dir / f"vector_failures_{date_str}.jsonl"
            
            # 각 실패 항목을 JSONL 형식으로 저장
            with open(dlq_file, 'a', encoding='utf-8') as f:
                for item in failed_items:
                    failed_record = {
                        "timestamp": datetime.now().isoformat(),
                        "error_type": "vectorization_failed",
                        "reason": item.get("failure_reason", "unknown"),
                        "data": {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "description": item.get("description", "")[:200],  # 처음 200자만
                        },
                        "metadata": {
                            "embedding_model": self.embedding_model,
                            "embedding_dimension": self.embedding_dimension
                        }
                    }
                    json.dump(failed_record, f, ensure_ascii=False)
                    f.write('\n')
            
            print(f"💀 벡터화 실패 {len(failed_items)}개 항목 Dead Letter 저장: {dlq_file}")
            
        except Exception as e:
            print(f"⚠️ Dead Letter 저장 실패: {e}")
    
    def _track_api_usage(self, tokens: int, response_time_ms: float, success: bool = True, error_type: str = None):
        """API 사용량 추적 (MVP 버전)"""
        try:
            # OpenAI 최신 가격 (per 1K tokens)
            # cf. https://platform.openai.com/docs/pricing
            model_costs = {
                'text-embedding-ada-002': 0.00005, 
                'text-embedding-3-small': 0.00001, 
                'text-embedding-3-large': 0.000065,
                'text-embedding-3': 0.000065, 
            }
            
            cost_per_1k = model_costs.get(self.embedding_model, 0.00005)
            cost = (tokens / 1000) * cost_per_1k
            
            # 로깅 (달러 + 한화)
            krw_cost = cost * 1500  # 1달러 = 1500원 기준 (보수적 추정치)
            if success:
                print(f"      📊 API: {tokens} 토큰, ${cost:.6f} (₩{krw_cost:.2f}), {response_time_ms:.0f}ms")
            else:
                print(f"      ❌ API 실패: {error_type}")
                
            return cost
            
        except Exception as e:
            print(f"⚠️ API 사용량 추적 실패: {e}")
            return 0.0
    
    def _generate_description_from_dict(self, item: Dict) -> str:
        """RAG 최적화된 설명 텍스트 생성"""
        # 크롤러 데이터 구조에 맞춰 필드명 수정
        duration = item.get('duration_minutes', item.get('duration', 60))
        price = item.get('price_per_person', item.get('price', 0))
        activity_level = item.get('activity_level', 2)
        difficulty_level = item.get('difficulty_level', 3)
        group_min = item.get('group_size_min', 2)
        group_max = item.get('group_size_max', 4)
        rating = item.get('rating', 0)
        
        # RAG: 구조화된 텍스트 + 자연어 조합
        activity_text = {1: "거의 없음", 2: "보통", 3: "많음"}.get(activity_level, "보통")
        difficulty_text = {1: "매우 쉬움", 2: "쉬움", 3: "보통", 4: "어려움", 5: "매우 어려움"}.get(difficulty_level, "보통")
        
        # 🎯 RAG: 검색성 + 의미적 풍부함
        structured_part = f"""
방탈출 테마: {item['name']}
운영업체: {item.get('company', '정보없음')}
위치: {item['region']} {item.get('sub_region', '')}
장르: {item['theme']}
난이도: {difficulty_text} ({difficulty_level}/5)
활동성: {activity_text} ({activity_level}/3)
소요시간: {duration}분
참여인원: {group_min}-{group_max}명
1인당 가격: {'가격 정보 없음' if price == 0 else f'{price:,}원'}
평점: {rating}점
        """.strip()
        
        # 자연어 방탈출 테마 설명 
        description = item.get('description', '')
        
        # 🚀 개선: 검색 키워드 보강
        keywords = self._extract_search_keywords(item)
        
        natural_part = f"""
이 방탈출은 {item['region']} {item.get('sub_region', '')}에 위치한 {item['theme']} 테마입니다.
{item.get('company', '업체')}에서 운영하며, {group_min}명부터 {group_max}명까지 참여 가능합니다.
난이도는 {difficulty_text}이고, 활동성은 {activity_text} 수준입니다.
소요시간은 약 {duration}분이며, 1인당 {'가격 정보 없음' if price == 0 else f'{price:,}원'}입니다.
"""
        
        if description:
            natural_part += f"\n스토리: {description}"
        
        if keywords:
            natural_part += f"\n관련 키워드: {', '.join(keywords)}"
        
        return f"{structured_part}\n\n{natural_part}".strip()
    
    def _extract_search_keywords(self, item: Dict) -> List[str]:
        """🚀 실무 개선: 유연한 키워드 추출"""
        keywords = []
        
        # 🎯 테마 기반 키워드
        theme = item.get('theme', '').lower()
        keywords.extend(self._extract_theme_keywords(theme))
        
        # 난이도 기반 키워드
        difficulty = item.get('difficulty_level', 3)
        keywords.extend(self._extract_difficulty_keywords(difficulty))
        
        # 인원 기반 키워드  
        group_min = item.get('group_size_min', 2)
        group_max = item.get('group_size_max', 4)
        keywords.extend(self._extract_group_keywords(group_min, group_max))
        
        # 가격 기반 키워드
        price = item.get('price_per_person', 0)
        keywords.extend(self._extract_price_keywords(price))
        
        # 지역 기반 키워드 (동적 추출)
        region = item.get('region', '')
        sub_region = item.get('sub_region', '')
        keywords.extend(self._extract_location_keywords(region, sub_region))
        
        # 🚀 신규: 설명에서 키워드 자동 추출
        description = item.get('description', '')
        keywords.extend(self._extract_description_keywords(description))
        
        return list(set(keywords))  # 중복 제거
    
    def _extract_theme_keywords(self, theme: str) -> List[str]:
        """테마별 키워드 추출 (유연한 매칭)"""
        keywords = []
        
        # 기본 하드코딩 매핑 (핵심 테마만)
        base_mappings = {
            '추리': ['추리', '수사', '탐정', '범죄', '미스터리'],
            '공포': ['공포', '무서운', '좀비', '유령', '호러'],
            '모험': ['모험', '탐험', '어드벤처', '액션'],
            '로맨스': ['로맨스', '연인', '커플', '사랑'],
            '코미디': ['코미디', '웃긴', '재밌는', '유머'],
            'sf': ['SF', '사이파이', '미래', '우주', '로봇'],
            '판타지': ['판타지', '마법', '중세', '기사'],
            '스릴러': ['스릴러', '긴장감', '서스펜스']
        }
        
        # 정확 매칭
        for theme_key, theme_words in base_mappings.items():
            if theme_key in theme:
                keywords.extend(theme_words)
        
        # 🚀 새로운 테마 자동 처리 
        if not keywords:  # 매핑에 없는 테마
            # 테마명 자체를 키워드로 추가
            if theme and len(theme) > 1:
                keywords.append(theme)
                
                # 유사한 의미 추론 
                if any(word in theme for word in ['좀비', '귀신', '괴물']):
                    keywords.extend(['공포', '무서운'])
                elif any(word in theme for word in ['사랑', '연애', '데이트']):
                    keywords.extend(['로맨스', '커플'])
                elif any(word in theme for word in ['웃음', '재미', '개그']):
                    keywords.extend(['코미디', '재밌는'])
                elif any(word in theme for word in ['어려운', '극한', '도전']):
                    keywords.extend(['고급', '도전적'])
        
        return keywords
    
    def _extract_difficulty_keywords(self, difficulty: int) -> List[str]:
        """난이도 키워드 (세분화)"""
        if difficulty <= 1:
            return ['매우 쉬운', '입문자', '처음']
        elif difficulty <= 2:
            return ['쉬운', '초보자', '입문']
        elif difficulty >= 5:
            return ['극한', '전문가', '최고난이도']
        elif difficulty >= 4:
            return ['어려운', '고급', '도전적']
        else:
            return ['보통', '일반']
    
    def _extract_group_keywords(self, group_min: int, group_max: int) -> List[str]:
        """인원수 키워드"""
        keywords = []
        
        if group_min == 2 and group_max <= 4:
            keywords.extend(['커플', '소규모', '데이트'])
        elif group_max >= 8:
            keywords.extend(['대규모', '팀빌딩', '단체', '회사'])
        elif group_max >= 6:
            keywords.extend(['중규모', '가족', '친구들'])
        
        # 정확한 인원 키워드
        if group_min == group_max:
            keywords.append(f'{group_min}명 전용')
        
        return keywords
    
    def _extract_price_keywords(self, price: int) -> List[str]:
        """가격대 키워드"""
        if price < 15000:
            return ['저렴한', '가성비', '학생']
        elif price < 25000:
            return ['적당한', '일반적']
        elif price < 40000:
            return ['조금 비싼', '퀄리티']
        else:
            return ['프리미엄', '고급', '특별한']
    
    def _extract_location_keywords(self, region: str, sub_region: str) -> List[str]:
        """지역 키워드"""
        keywords = []
        
        # 지역 자체 추가
        if region:
            keywords.append(region)
        if sub_region:
            keywords.append(sub_region)
        
        # 특별한 지역 속성 
        location_attributes = {
            '강남': ['접근성 좋은', '지하철', '번화가'],
            '홍대': ['대학가', '젊은', '핫플'],
            '신촌': ['대학가', '젊은'],
            '명동': ['관광지', '접근성'],
            '잠실': ['롯데타워', '쇼핑'],
            '강북': ['조용한', '동네'],
            '대학로': ['공연', '문화']
        }
        
        for location, attrs in location_attributes.items():
            if location in sub_region:
                keywords.extend(attrs)
        
        return keywords
    
    def _extract_description_keywords(self, description: str) -> List[str]:
        """🚀 설명에서 키워드 자동 추출 (NLP 기법)"""
        if not description or len(description) < 10:
            return []
        
        keywords = []
        desc_lower = description.lower()
        
        # 감정/분위기 키워드 자동 감지
        emotion_patterns = {
            '무서운': ['무서', '공포', '두려', '소름', '떨림'],
            '재밌는': ['재미', '웃음', '즐거', '신나', '유쾌'],
            '로맨틱한': ['사랑', '연인', '로맨', '달콤', '설렘'],
            '긴장감': ['긴장', '스릴', '짜릿', '심장', '아찔'],
            '신비로운': ['신비', '마법', '환상', '신기', '놀라운']
        }
        
        for emotion, patterns in emotion_patterns.items():
            if any(pattern in desc_lower for pattern in patterns):
                keywords.append(emotion)
        
        # 행동 키워드 감지
        action_patterns = {
            '추리': ['추리', '수사', '단서', '범인', '사건'],
            '탈출': ['탈출', '도망', '빠져나', '벗어나'],
            '협력': ['협력', '팀워크', '함께', '모두'],
            '도전': ['도전', '어려움', '극복', '해결']
        }
        
        for action, patterns in action_patterns.items():
            if any(pattern in desc_lower for pattern in patterns):
                keywords.append(action)
        
        return keywords
    
    def _chunk_text_if_needed(self, text: str, max_tokens: int = 8000) -> List[str]:
        """실무 RAG: 긴 텍스트 청킹"""
        # 간단한 토큰 추정 (TODO: 실제로는 tiktoken 사용 권장)
        estimated_tokens = len(text.split()) * 1.3  # 한국어는 약 1.3 토큰/단어
        
        if estimated_tokens <= max_tokens:
            return [text]
        
        # 문단 기반 청킹
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        
        for paragraph in paragraphs:
            paragraph_tokens = len(paragraph.split()) * 1.3
            current_tokens = len(current_chunk.split()) * 1.3
            
            if current_tokens + paragraph_tokens > max_tokens and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = paragraph
            else:
                current_chunk += f"\n\n{paragraph}" if current_chunk else paragraph
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks
    
    def _generate_text_hash(self, text: str) -> str:
        """텍스트 해시 생성 (중복 벡터화 방지)"""
        # 정규화: 공백, 줄바꿈 통일
        normalized = re.sub(r'\s+', ' ', text.strip().lower())
        return hashlib.md5(normalized.encode('utf-8')).hexdigest()
    
    
    async def _detect_vector_dimension(self):
        """벡터 모델의 차원을 동적으로 감지"""
        if self.embedding_dimension is not None:
            return  # 이미 감지됨
            
        try:
            # 테스트 텍스트로 벡터 생성하여 차원 확인
            response = await self.openai_client.embeddings.create(
                model=self.embedding_model,
                input="test"
            )
            self.embedding_dimension = len(response.data[0].embedding)
            
        except Exception as e:
            print(f"⚠️ 벡터 차원 감지 실패: {e}")
            # 모델별 기본값 설정
            model_dimensions = {
                'text-embedding-ada-002': 1536,
                'text-embedding-3-small': 1536,
                'text-embedding-3-large': 3072,
                'text-embedding-3': 3072,
            }
            self.embedding_dimension = model_dimensions.get(self.embedding_model, 1536)
            print(f"🔧 기본값 사용: {self.embedding_dimension}차원")
    
    async def _generate_vector(self, text: str) -> List[float]:
        """OpenAI 벡터 생성 (실무 베스트 프랙티스: 재시도 + 상세 에러 핸들링)"""
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                # 텍스트 전처리 
                cleaned_text = self._preprocess_text_for_embedding(text)
                
                # 📊 MVP 메트릭: 간단한 토큰 추적
                start_time = time.time()
                estimated_tokens = len(cleaned_text.split()) * 1.3  # 한국어 추정
                
                response = await self.openai_client.embeddings.create(
                    model=self.embedding_model,
                    input=cleaned_text
                )
                
                # 📊 MVP 메트릭: API 호출 기록
                response_time_ms = (time.time() - start_time) * 1000
                actual_tokens = response.usage.total_tokens if hasattr(response, 'usage') else int(estimated_tokens)
                
                # 간단한 비용 계산 및 로깅
                actual_cost = self._track_api_usage(actual_tokens, response_time_ms, success=True)
                
                # 배치 비용에 실제 비용 누적
                if hasattr(self, '_current_batch_cost'):
                    self._current_batch_cost += actual_cost
                
                vector = response.data[0].embedding
                
                # 벡터 품질 검증 (실무 RAG 필수)
                if self._validate_vector_quality(vector):
                    return vector
                else:
                    raise ValueError("Invalid vector quality")
                
            except Exception as e:
                error_type = type(e).__name__
                
                # OpenAI 특화 에러 처리
                if "rate_limit" in str(e).lower():
                    delay = base_delay * (2 ** attempt) + 2  # 레이트 리밋시 더 긴 대기
                    print(f"🔄 Rate limit 재시도 {attempt+1}/{max_retries} (대기: {delay}초)")
                    await asyncio.sleep(delay)
                    continue
                elif "context_length" in str(e).lower():
                    # 텍스트가 너무 긴 경우 청킹
                    print(f"✂️ 텍스트 길이 초과 - 청킹 시도")
                    chunks = self._chunk_text_if_needed(text, max_tokens=6000)
                    if len(chunks) > 1:
                        # 첫 번째 청크만 사용 (간단한 폴백)
                        return await self._generate_vector(chunks[0])
                elif attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"⚠️ 벡터 생성 오류 ({error_type}): {e}")
                    print(f"🔄 재시도 {attempt+1}/{max_retries} (대기: {delay}초)")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"❌ 최종 벡터 생성 실패 ({error_type}): {e}")
                    # 실패 추적
                    self._track_api_usage(0, 0, success=False, error_type=error_type)
        
        # 🎯 모든 재시도 실패시 None 반환 (더미 벡터 대신)
        # 호출하는 곳에서 None 체크 후 DB 업데이트 스킵
        return None
    
    def _preprocess_text_for_embedding(self, text: str) -> str:
        """임베딩을 위한 텍스트 전처리 (실무 RAG 베스트 프랙티스)"""
        # 1. 기본 정리
        text = text.strip()
        
        # 2. 과도한 공백/줄바꿈 정리  
        text = re.sub(r'\n{3,}', '\n\n', text)  # 3개 이상 줄바꿈 → 2개
        text = re.sub(r' {2,}', ' ', text)      # 2개 이상 공백 → 1개
        
        # 3. 특수문자 정리 (검색 방해 요소)
        text = re.sub(r'[^\w\s가-힣.,!?():\-]', '', text)
        
        # 4. 길이 제한 (토큰 오버플로우 방지)
        if len(text) > 30000:  # 약 8000 토큰 추정
            text = text[:30000] + "..."
            
        return text
    
    def _validate_vector_quality(self, vector: List[float]) -> bool:
        """벡터 품질 검증"""
        if not vector:
            return False
            
        # 1. 모두 0인 벡터 (더미) 체크
        if all(v == 0.0 for v in vector):
            return False
            
        # 2. NaN/Inf 체크
        if any(not (-1e10 < v < 1e10) for v in vector):
            return False
            
        # 3. 벡터 노름 체크 (너무 작거나 큰 벡터)
        norm = sum(v * v for v in vector) ** 0.5
        if norm < 1e-6 or norm > 10:
            return False
            
        return True
    
    async def _generate_vectors_batch(self, texts: List[str]) -> List[List[float]]:
        """OpenAI 배치 벡터 생성"""
        try:
            # OpenAI API는 한 번에 최대 2048개 입력 지원
            max_batch_size = min(len(texts), 100)  # 안전하게 100개씩
            
            if len(texts) <= max_batch_size:
                # 한 번에 처리 가능
                start_time = time.time()
                response = await self.openai_client.embeddings.create(
                    model=self.embedding_model,
                    input=texts
                )
                
                # 실제 토큰 수 추적
                if hasattr(response, 'usage') and response.usage:
                    response_time_ms = (time.time() - start_time) * 1000
                    actual_cost = self._track_api_usage(response.usage.total_tokens, response_time_ms, success=True)
                    self._current_batch_cost = getattr(self, '_current_batch_cost', 0.0) + actual_cost
                
                return [item.embedding for item in response.data]
            else:
                # 청크로 나눠서 처리
                all_vectors = []
                for i in range(0, len(texts), max_batch_size):
                    chunk = texts[i:i + max_batch_size]
                    start_time = time.time()
            response = await self.openai_client.embeddings.create(
                model=self.embedding_model,
                        input=chunk
                    )
                    
                    # 실제 토큰 수 추적
                    if hasattr(response, 'usage') and response.usage:
                        response_time_ms = (time.time() - start_time) * 1000
                        actual_cost = self._track_api_usage(response.usage.total_tokens, response_time_ms, success=True)
                        self._current_batch_cost = getattr(self, '_current_batch_cost', 0.0) + actual_cost
                    
                    chunk_vectors = [item.embedding for item in response.data]
                    all_vectors.extend(chunk_vectors)
                
                return all_vectors
            
        except Exception as e:
            print(f"⚠️ 배치 벡터 생성 오류: {e}")
            # 🎯 실패 시 빈 리스트 반환 (더미 벡터 대신)
            # 호출하는 곳에서 "벡터 수 불일치"로 처리됨
            return []
    

    

async def main():
    """메인 실행 함수"""
    generator = VectorGenerator()
    
    # 테스트 모드 여부 확인 
    test_mode = "--test" in sys.argv
    
    if test_mode:
        print("🧪 테스트 모드 시작")
        await generator.generate_vectors(test_mode=True, test_limit=2)
    else:
        print("🔧 DB 데이터 벡터화 모드 (전체)")
        print("💡 테스트하려면: python vector_generator.py --test")
        await generator.generate_vectors()

if __name__ == "__main__":
    asyncio.run(main())