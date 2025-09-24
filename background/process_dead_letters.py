"""
Dead Letter Queue 재처리 스크립트
실패한 데이터들을 다시 처리 시도

사용법:
  python process_dead_letters.py                    # 모든 실패 데이터 재처리
  python process_dead_letters.py crawler 20240122   # 특정 날짜 크롤러 실패 재처리  
  python process_dead_letters.py vector 20240122    # 특정 날짜 벡터 실패 재처리
"""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

# 프로젝트 루트를 Python path에 추가
sys.path.append(str(Path(__file__).parent.parent))

from data_crawler import BackroomCrawler, EscapeRoomData
from vector_generator import VectorGenerator


class DeadLetterProcessor:
    """Dead Letter Queue 재처리기"""
    
    def __init__(self):
        self.dlq_dir = Path("data/dead_letters")
        self.success_count = 0
        self.failure_count = 0
        
    async def process_all_dead_letters(self):
        """모든 Dead Letter 파일 재처리"""
        if not self.dlq_dir.exists():
            print("📭 Dead Letter 디렉토리가 없습니다.")
            return
            
        dlq_files = list(self.dlq_dir.glob("*.jsonl"))
        if not dlq_files:
            print("📭 처리할 Dead Letter 파일이 없습니다.")
            return
            
        print(f"📋 발견된 Dead Letter 파일: {len(dlq_files)}개")
        
        for dlq_file in dlq_files:
            print(f"\n🔄 처리 중: {dlq_file.name}")
            
            if "crawler_failures" in dlq_file.name:
                await self._process_crawler_failures(dlq_file)
            elif "vector_failures" in dlq_file.name:
                await self._process_vector_failures(dlq_file)
            else:
                print(f"⚠️ 알 수 없는 파일 형식: {dlq_file.name}")
        
        print(f"\n📊 재처리 완료: 성공 {self.success_count}개, 실패 {self.failure_count}개")
    
    async def process_specific_dead_letters(self, failure_type: str, date: str):
        """특정 타입과 날짜의 Dead Letter 재처리"""
        if failure_type == "crawler":
            filename = f"crawler_failures_{date}.jsonl"
        elif failure_type == "vector":
            filename = f"vector_failures_{date}.jsonl"
        else:
            print(f"❌ 잘못된 타입: {failure_type} (crawler 또는 vector)")
            return
            
        dlq_file = self.dlq_dir / filename
        if not dlq_file.exists():
            print(f"📭 파일이 없습니다: {dlq_file}")
            return
            
        print(f"🔄 재처리 시작: {dlq_file.name}")
        
        if failure_type == "crawler":
            await self._process_crawler_failures(dlq_file)
        else:
            await self._process_vector_failures(dlq_file)
            
        print(f"📊 재처리 완료: 성공 {self.success_count}개, 실패 {self.failure_count}개")
    
    async def _process_crawler_failures(self, dlq_file: Path):
        """크롤러 실패 데이터 재처리"""
        print("🕷️ 크롤러 실패 데이터 재처리 중...")
        
        crawler = BackroomCrawler()
        await crawler.db.init()
        
        try:
            with open(dlq_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        record = json.loads(line.strip())
                        
                        if record.get("error_type") == "db_save_failed":
                            # DB 저장 실패 재시도
                            data_dict = record["data"]
                            escape_room = EscapeRoomData(
                                name=data_dict["name"],
                                region=data_dict["region"],
                                sub_region=data_dict["sub_region"],
                                theme=data_dict["theme"],
                                duration=data_dict["duration_minutes"],
                                price=data_dict["price_per_person"],
                                description=data_dict["description"],
                                company=data_dict["company"],
                                rating=data_dict.get("rating"),
                                image_url=data_dict.get("image_url", ""),
                                source_url=data_dict.get("source_url", ""),
                                booking_url=data_dict.get("booking_url", ""),
                                difficulty_level=data_dict.get("difficulty_level", 3),
                                activity_level=data_dict.get("activity_level", 2),
                                group_size_min=data_dict.get("group_size_min", 2),
                                group_size_max=data_dict.get("group_size_max", 4)
                            )
                            
                            success = await crawler._save_to_database(escape_room)
                            if success:
                                print(f"  ✅ 라인 {line_num}: {escape_room.name}")
                                self.success_count += 1
                            else:
                                print(f"  ❌ 라인 {line_num}: {escape_room.name} (재실패)")
                                self.failure_count += 1
                        else:
                            print(f"  ⚠️ 라인 {line_num}: 추출 실패는 재처리 불가")
                            self.failure_count += 1
                            
                    except Exception as e:
                        print(f"  ❌ 라인 {line_num} 처리 오류: {e}")
                        self.failure_count += 1
                        
        finally:
            crawler.teardown_driver()
    
    async def _process_vector_failures(self, dlq_file: Path):
        """벡터화 실패 데이터 재처리"""
        print("🔢 벡터화 실패 데이터 재처리 중...")
        
        generator = VectorGenerator()
        
        # PostgreSQL 연결
        import asyncpg

        from app.core.config import settings
        conn = await asyncpg.connect(settings.database_url)
        
        try:
            with open(dlq_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        record = json.loads(line.strip())
                        
                        if record.get("error_type") == "vectorization_failed":
                            data = record["data"]
                            escape_room_id = data["id"]
                            
                            # DB에서 최신 데이터 다시 조회
                            row = await conn.fetchrow("""
                                SELECT id, name, description, theme, region, sub_region, company,
                                       difficulty_level, activity_level, duration_minutes, 
                                       price_per_person, group_size_min, group_size_max, rating
                                FROM escape_rooms 
                                WHERE id = $1 AND embedding IS NULL
                            """, escape_room_id)
                            
                            if not row:
                                print(f"  ⚠️ 라인 {line_num}: ID {escape_room_id} - 이미 벡터화됨 또는 삭제됨")
                                continue
                            
                            # 벡터 재생성 시도
                            item_dict = dict(row)
                            description = generator._generate_description_from_dict(item_dict)
                            vector = await generator._generate_vector(description)
                            
                            # 더미 벡터 체크
                            if all(v == 0.0 for v in vector):
                                print(f"  ❌ 라인 {line_num}: {data['name']} (여전히 더미 벡터)")
                                self.failure_count += 1
                                continue
                            
                            # DB 업데이트
                            await conn.execute("""
                                UPDATE escape_rooms 
                                SET embedding = $1 
                                WHERE id = $2
                            """, vector, escape_room_id)
                            
                            print(f"  ✅ 라인 {line_num}: {data['name']} (ID: {escape_room_id})")
                            self.success_count += 1
                        else:
                            print(f"  ⚠️ 라인 {line_num}: 알 수 없는 오류 타입")
                            self.failure_count += 1
                            
                    except Exception as e:
                        print(f"  ❌ 라인 {line_num} 처리 오류: {e}")
                        self.failure_count += 1
                        
        finally:
            await conn.close()
    
    def archive_processed_files(self):
        """처리 완료된 Dead Letter 파일들을 아카이브"""
        try:
            archive_dir = self.dlq_dir / "processed"
            archive_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            processed_files = []
            for dlq_file in self.dlq_dir.glob("*.jsonl"):
                if dlq_file.parent.name != "processed":  # 이미 아카이브된 파일 제외
                    archive_file = archive_dir / f"{dlq_file.stem}_{timestamp}.jsonl"
                    dlq_file.rename(archive_file)
                    processed_files.append(archive_file.name)
            
            if processed_files:
                print(f"📦 {len(processed_files)}개 파일 아카이브 완료: {archive_dir}")
            
        except Exception as e:
            print(f"⚠️ 아카이브 실패: {e}")

async def main():
    """메인 실행 함수"""
    processor = DeadLetterProcessor()
    
    if len(sys.argv) == 1:
        # 모든 Dead Letter 재처리
        await processor.process_all_dead_letters()
        
    elif len(sys.argv) == 3:
        # 특정 타입과 날짜 재처리
        failure_type = sys.argv[1]
        date = sys.argv[2]
        await processor.process_specific_dead_letters(failure_type, date)
        
    else:
        print("사용법:")
        print("  python process_dead_letters.py                    # 모든 실패 데이터 재처리")
        print("  python process_dead_letters.py crawler 20240122   # 특정 날짜 크롤러 실패 재처리")  
        print("  python process_dead_letters.py vector 20240122    # 특정 날짜 벡터 실패 재처리")
        return
    
    # 성공적으로 처리된 경우 아카이브
    if processor.success_count > 0:
        processor.archive_processed_files()

if __name__ == "__main__":
    asyncio.run(main())
