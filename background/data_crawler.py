"""
백룸 사이트 크롤러 - 방탈출 데이터 수집
사용법: 가상환경에서 python background/data_crawler.py
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import random
import re
import sys
import traceback
from typing import Any, Dict, List

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# 프로젝트 루트를 Python path에 추가
sys.path.append(str(Path(__file__).parent.parent))

# 환경변수 로드
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

from app.core.config import settings
from app.core.postgres_manager import PostgresManager

# 사이트의 지역 및 서브지역 매핑 데이터 
# 주석처리로 특정 지역/서브지역 제외 가능
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

# 크롤링에서 제외할 지역들 (테스트나 부분 실행용)
# 사용법: 제외하고 싶은 지역을 리스트에 추가
# 예시: EXCLUDED_REGIONS = ['경기', '부산', '대구']  # 이 지역들은 크롤링하지 않음
EXCLUDED_REGIONS = []

# 크롤링에서 제외할 서브지역들 (지역별로 설정)  
# 사용법: 지역별로 제외하고 싶은 서브지역을 딕셔너리에 추가
# 예시: EXCLUDED_SUB_REGIONS = {
#     '서울': ['강남', '홍대'],        # 서울에서 강남, 홍대만 제외
#     '부산': ['해운대구', '금정구'],   # 부산에서 해운대구, 금정구 제외
#     '경기': ['수원', '성남']         # 경기에서 수원, 성남 제외
# }
EXCLUDED_SUB_REGIONS = {}

@dataclass
class CrawlingState:
    """크롤링 진행 상태 추적"""
    current_region: str = ""
    current_sub_region: str = ""
    current_page: int = 1
    total_collected: int = 0
    last_processed_theme: str = ""
    completed_regions: List[str] = None
    completed_sub_regions: Dict[str, List[str]] = None
    
    def __post_init__(self):
        if self.completed_regions is None:
            self.completed_regions = []
        if self.completed_sub_regions is None:
            self.completed_sub_regions = {}

@dataclass
class EscapeRoomData:
    """수집된 방탈출 데이터 - DB 스키마와 매칭"""
    name: str
    region: str
    sub_region: str
    theme: str
    duration: int  # 분 (duration_minutes)
    price: int  # 1인당 가격 (price_per_person)
    description: str = ""
    company: str = ""
    rating: float = None  # 평점 (4.8 같은)
    image_url: str = ""   # 포스터 이미지 URL
    source_url: str = ""  # 원본 URL
    booking_url: str = ""  # 예약하러가기 URL
    
    # DB 저장시 추가될 필드들 (기본값)
    difficulty_level: int = 3  # 1-5, 기본값 3 (보통)
    activity_level: int = 2    # 1-3, 기본값 2 (거의없음, 보통, 있음)
    group_size_min: int = 2    # 최소 인원
    group_size_max: int = 6    # 최대 인원
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'region': self.region,
            'sub_region': self.sub_region,
            'theme': self.theme,
            'duration_minutes': self.duration,  
            'price_per_person': self.price,    
            'description': self.description,
            'company': self.company,
            'rating': self.rating,
            'image_url': self.image_url,
            'source_url': self.source_url,
            'booking_url': self.booking_url,
            'difficulty_level': self.difficulty_level,
            'activity_level': self.activity_level,
            'group_size_min': self.group_size_min,
            'group_size_max': self.group_size_max
        }

class BackroomCrawler:
    """백룸 사이트 크롤러"""
    
    def __init__(self, headless: bool = None):
        self.base_url = settings.crawl_base_url
        self.data: List[EscapeRoomData] = []
        self.headless = headless if headless is not None else settings.crawl_headless
        self.driver = None
        
        # 크롤링 설정 (환경변수에서 가져오기)
        self.wait_time = settings.crawl_wait_time
        self.page_timeout = settings.crawl_page_timeout
        
        # 크롤링 상태 추적
        self.state = CrawlingState()
        self.state_file = Path("data/crawling_state.json")
        self.state_file.parent.mkdir(exist_ok=True)
        
        # PostgreSQL 연결 초기화
        self.db = PostgresManager()
        print("🗄️ PostgreSQL 연결 매니저 생성 완료")
        
    def _random_wait(self, base_time: float = None) -> float:
        """랜덤 대기 시간 생성 (봇 탐지 방지)"""
        if base_time is None:
            base_time = self.wait_time
        # ±50% 랜덤 변동
        random_factor = random.uniform(0.5, 1.5)
        return base_time * random_factor
        
    def setup_driver(self):
        """Chrome 드라이버 설정"""
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        # 실제 브라우저처럼 보이게 하는 User-Agent
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # 봇 탐지 방지 추가 설정
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.wait = WebDriverWait(self.driver, self.page_timeout)
        
        # WebDriver 탐지 방지 스크립트 실행
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
    def teardown_driver(self):
        """드라이버 종료"""
        if self.driver:
            self.driver.quit()
    
    def save_state(self):
        """현재 크롤링 상태 저장"""
        try:
            state_dict = {
                'current_region': self.state.current_region,
                'current_sub_region': self.state.current_sub_region,
                'current_page': self.state.current_page,
                'total_collected': self.state.total_collected,
                'last_processed_theme': self.state.last_processed_theme,
                'completed_regions': self.state.completed_regions,
                'completed_sub_regions': self.state.completed_sub_regions
            }
            
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state_dict, f, ensure_ascii=False, indent=2)
                
            print(f"📁 상태 저장: {self.state.current_region} > {self.state.current_sub_region} (페이지 {self.state.current_page})")
        except Exception as e:
            print(f"⚠️ 상태 저장 실패: {e}")
    
    def load_state(self) -> bool:
        """저장된 크롤링 상태 로드"""
        try:
            if not self.state_file.exists():
                return False
                
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state_dict = json.load(f)
            
            self.state.current_region = state_dict.get('current_region', '')
            self.state.current_sub_region = state_dict.get('current_sub_region', '')
            self.state.current_page = state_dict.get('current_page', 1)
            self.state.total_collected = state_dict.get('total_collected', 0)
            self.state.last_processed_theme = state_dict.get('last_processed_theme', '')
            self.state.completed_regions = state_dict.get('completed_regions', [])
            self.state.completed_sub_regions = state_dict.get('completed_sub_regions', {})
            
            print(f"📂 상태 로드: {self.state.current_region} > {self.state.current_sub_region} (페이지 {self.state.current_page})")
            print(f"   지금까지 수집: {self.state.total_collected}개, 마지막 테마: {self.state.last_processed_theme}")
            return True
        except Exception as e:
            print(f"⚠️ 상태 로드 실패: {e}")
            return False
    
    def update_state(self, region: str = None, sub_region: str = None, page: int = None, theme_name: str = None):
        """크롤링 상태 업데이트"""
        if region:
            self.state.current_region = region
        if sub_region:
            self.state.current_sub_region = sub_region
        if page:
            self.state.current_page = page
        if theme_name:
            self.state.last_processed_theme = theme_name
        
        # 🎯 메모리 기반 카운트 대신 DB 카운트 사용
        self.state.total_collected = self.state.total_collected + 1 if theme_name else self.state.total_collected
        self.save_state()
    
    def mark_region_completed(self, region: str):
        """지역 완료 표시"""
        if region not in self.state.completed_regions:
            self.state.completed_regions.append(region)
        print(f"✅ {region} 지역 완료!")
    
    def mark_sub_region_completed(self, region: str, sub_region: str):
        """서브지역 완료 표시"""
        if region not in self.state.completed_sub_regions:
            self.state.completed_sub_regions[region] = []
        if sub_region not in self.state.completed_sub_regions[region]:
            self.state.completed_sub_regions[region].append(sub_region)
        print(f"✅ {region} > {sub_region} 완료!")
    
            
    async def crawl_all_regions(self) -> List[EscapeRoomData]:
        """모든 지역 크롤링 - 상태 추적 시스템"""
        print("🚀 백룸 크롤링 시작...")
        
        # 이전 상태 로드 시도
        resumed = self.load_state()
        if resumed:
            print(f"🔄 이전 세션에서 이어서 진행: {self.state.current_region} > {self.state.current_sub_region}")
        
        self.setup_driver()
        
        try:
            # 0. PostgreSQL 연결 초기화
            print("🗄️ PostgreSQL 연결 초기화 중...")
            await self.db.init()
            print("✅ PostgreSQL 연결 완료")
            
            # 1. 메인 페이지 접속 및 로딩 확인
            await self._load_main_page()
            
            # 2. nav 내 지역 버튼 영역 찾기
            nav_region_area = await self._find_region_navigation()
            if not nav_region_area:
                return []
            
            # 3. 크롤링할 지역 결정 (상태 기반 + 필터링)
            regions_to_crawl = []
            if self.state.current_region and self.state.current_region not in EXCLUDED_REGIONS:
                # 이어서 할 지역부터 시작 (단, 제외 목록에 없는 경우만)
                regions_to_crawl = [self.state.current_region]
            else:
                # 처음 시작 OR 현재 지역이 제외 목록에 있는 경우
                # → 전체 지역에서 제외 목록 빼고 크롤링
                all_regions = list(REGION_DATA.keys())
                regions_to_crawl = [region for region in all_regions if region not in EXCLUDED_REGIONS]
                
                # 현재 지역이 제외된 경우, 다음 지역부터 시작
                if self.state.current_region in EXCLUDED_REGIONS:
                    current_index = list(REGION_DATA.keys()).index(self.state.current_region)
                    remaining_regions = list(REGION_DATA.keys())[current_index + 1:]
                    regions_to_crawl = [region for region in remaining_regions if region not in EXCLUDED_REGIONS]
                    print(f"🔄 제외된 지역({self.state.current_region}) 건너뛰고 다음 지역부터: {regions_to_crawl[:3]}...")
                
                # 테스트용으로 서울만 (필요시 주석 해제)
                # regions_to_crawl = ["서울"]
            
            print(f"📍 크롤링 지역: {regions_to_crawl}")
            
            if EXCLUDED_REGIONS:
                print(f"🚫 제외된 지역: {EXCLUDED_REGIONS}")
            if EXCLUDED_SUB_REGIONS:
                print(f"🚫 제외된 서브지역 설정: {EXCLUDED_SUB_REGIONS}")
            
            for region_name in regions_to_crawl:
                print(f"\n🏢 지역: {region_name} 크롤링 시작...")
                self.update_state(region=region_name, sub_region="전체", page=1)
                
                # 지역 버튼 클릭
                if await self._click_region_button(region_name):
                    print(f"✅ {region_name} 지역 선택 완료!")
                    
                    # 서브지역들 크롤링 (필터링 적용)
                    all_sub_regions = REGION_DATA.get(region_name, [])
                    excluded_sub_regions = EXCLUDED_SUB_REGIONS.get(region_name, [])
                    sub_regions = [sub for sub in all_sub_regions if sub not in excluded_sub_regions]
                    
                    print(f"📍 {region_name}의 서브지역: {sub_regions}")
                    if excluded_sub_regions:
                        print(f"  🚫 제외된 서브지역: {excluded_sub_regions}")
                    
                    for j, sub_region in enumerate(sub_regions):
                        print(f"\n  🏘️ 서브지역 {j+1}/{len(sub_regions)}: {region_name} > {sub_region}")
                        self.update_state(region=region_name, sub_region=sub_region, page=1)
                        
                        # 서브지역 버튼 클릭
                        if await self._click_subregion_button(sub_region):
                            print(f"    ✅ {sub_region} 서브지역 선택 완료!")
                            
                            # 페이지별 크롤링 (상태 추적)
                            start_page = self.state.current_page if resumed and self.state.current_region == region_name and self.state.current_sub_region == sub_region else 1
                            page = start_page
                            
                            while True:
                                print(f"        📄 페이지 {page} 크롤링 중...")
                                self.update_state(page=page)
                                
                                # 현재 페이지의 카드들을 하나씩 클릭하여 상세 정보 수집
                                cards_processed = await self._process_current_page_cards(region_name, sub_region, page)
                                print(f"          ✅ {cards_processed}개 카드 처리 완료")
                                
                                # 🔍 DEBUG: 페이지 완료 후 배치 저장 (새로 수집된 데이터만)
                                if cards_processed > 0:
                                    # 이번 페이지에서 새로 수집된 데이터만 추출
                                    total_data_count = len(self.data)
                                    new_data = self.data[-cards_processed:] if cards_processed <= total_data_count else self.data
                                    
                                    print(f"          💾 배치 저장 시작: {len(new_data)}개 데이터 (전체: {total_data_count}개)")
                                    print(f"             🔍 첫 번째 데이터: {new_data[0].name if new_data else 'N/A'}")
                                    
                                    saved_count = await self._batch_save_to_database(new_data)
                                    
                                    if saved_count < len(new_data):
                                        print(f"          ⚠️ 일부 저장 실패: {saved_count}/{len(new_data)}개만 저장")
                                    else:
                                        print(f"          ✅ 배치 저장 성공: {saved_count}개 모두 저장")
                                        # 성공한 데이터는 메모리에서 제거
                                        self.data = self.data[:-saved_count] if saved_count <= len(self.data) else []
                                        print(f"          🧹 메모리 정리: 남은 데이터 {len(self.data)}개")
                                else:
                                    print(f"          ⚠️ 처리된 카드 없음 - 배치 저장 건너뜀")
                                
                                # 다음 페이지로 이동 시도
                                if not await self._go_to_next_page():
                                    print(f"        🏁 마지막 페이지 도달 (총 {page}페이지)")
                                    break
                                    
                                page += 1
                                await asyncio.sleep(self._random_wait())
                            
                            # 서브지역 완료 표시
                            self.mark_sub_region_completed(region_name, sub_region)
                            
                            # 다음 서브지역으로 이동하기 전 필터 해제 (마지막이 아닌 경우)
                            if j < len(sub_regions) - 1:
                                await self._clear_current_subregion_filter()
                                
                        else:
                            print(f"    ⚠️ {sub_region} 서브지역 버튼 클릭 실패")
                    
                    # 지역 완료 표시
                    self.mark_region_completed(region_name)
                    # 다음 지역으로 계속 진행
                else:
                    print(f"⚠️ {region_name} 지역 버튼 클릭 실패")
                
        except Exception as e:
            print(f"❌ 크롤링 오류: {e}")
            print(f"💾 현재 상태 저장 중... (지역: {self.state.current_region}, 페이지: {self.state.current_page})")
            self.save_state()
            import traceback
            traceback.print_exc()
            
        finally:
            self.teardown_driver()
            
        print(f"\n✅ 크롤링 완료! 총 {self.state.total_collected}개 데이터 처리")
        print(f"📊 최종 상태: {self.state.current_region} > {self.state.current_sub_region} (페이지 {self.state.current_page})")
        return []  # 메모리 절약: 빈 리스트 반환
    
    async def _process_current_page_cards(self, region_name: str, sub_region: str = "전체", page: int = 1) -> int:
        """현재 페이지의 카드들을 하나씩 클릭하여 상세 정보 수집 (정확한 시나리오)"""
        processed_count = 0
        
        try:
            # 페이지 로딩 대기
            await asyncio.sleep(3)
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "li")))
            
            # 정확한 카드 목록 찾기
            card_list_selectors = [
                "//ul[contains(@class, 'style__CardList-sc-82k06s-3') and contains(@class, 'bHkimQ')]",
                "//ul[contains(@class, 'CardList')]",
                "//ul[contains(@class, 'bHkimQ')]"
            ]
            
            card_list = None
            for selector in card_list_selectors:
                try:
                    card_list = self.driver.find_element(By.XPATH, selector)
                    if card_list:
                        print(f"        ✅ 카드 목록 발견: {selector}")
                        break
                except:
                    continue
            
            if not card_list:
                print("        ⚠️ 카드 목록을 찾을 수 없습니다")
                return 0
            
            # 카드 li 요소들 찾기
            cards = card_list.find_elements(By.XPATH, ".//li[contains(@class, 'style__Li_SolidThemeCardContainer-sc-iizpjd-1')]")
            if not cards:
                cards = card_list.find_elements(By.TAG_NAME, "li")
            
            print(f"        🎴 총 {len(cards)}개 카드 발견")
            
            # 이미 처리된 카드 수 확인 (재시작 지원) - 같은 페이지에서만 적용
            already_processed = 0
            if self.state.last_processed_theme and page == self.state.current_page:
                # 같은 페이지에서만 재시작 로직 적용
                print(f"        🔄 같은 페이지 재시작 확인: '{self.state.last_processed_theme}' 찾는 중...")
                for i in range(len(cards)):
                    try:
                        card = cards[i]
                        name_elem = card.find_element(By.XPATH, ".//label[contains(@class, 'style__Label_SolidCardTitle-sc-iizpjd-8')]")
                        card_name = name_elem.text.strip()
                        
                        # 이미 처리된 카드인지 확인
                        if card_name == self.state.last_processed_theme:
                            already_processed = i + 1  # 다음 카드부터 시작
                            print(f"        🔄 재시작: '{card_name}' 까지 처리 완료, {already_processed+1}번째 카드부터 시작")
                            break
                    except:
                        continue
                        
                if already_processed == 0:
                    print(f"        ✨ 새 페이지: '{self.state.last_processed_theme}' 없음, 1번째 카드부터 시작")
            else:
                print(f"        ✨ 새 페이지 또는 첫 실행: 1번째 카드부터 시작")
            
            # 각 카드를 순차적으로 처리 (스킵 적용)
            start_index = already_processed
            for i in range(start_index, len(cards)):
                try:
                    print(f"        🎯 카드 {i+1}/{len(cards)} 처리 중...")
                    
                    # 매번 새로 카드 목록을 찾아서 stale reference 방지
                    card_list = None
                    for selector in card_list_selectors:
                        try:
                            card_list = self.driver.find_element(By.XPATH, selector)
                            if card_list:
                                break
                        except:
                            continue
                    
                    if not card_list:
                        print(f"          ⚠️ 카드 목록 재검색 실패")
                        break
                    
                    # i번째 카드 다시 찾기
                    current_cards = card_list.find_elements(By.XPATH, ".//li[contains(@class, 'style__Li_SolidThemeCardContainer-sc-iizpjd-1')]")
                    if not current_cards:
                        current_cards = card_list.find_elements(By.TAG_NAME, "li")
                    
                    if i >= len(current_cards):
                        print(f"          ⚠️ 카드 {i+1} 찾을 수 없음 (총 {len(current_cards)}개)")
                        break
                    
                    current_card = current_cards[i]
                    
                    # 1단계: 목록에서 기본 정보 추출
                    basic_info = await self._extract_basic_info_from_card(current_card, region_name, sub_region)
                    if not basic_info:
                        print(f"          ⚠️ 카드 {i+1} 기본 정보 추출 실패")
                        continue
                    
                    # 중복 체크: 완벽하게 동일한 region + sub_region + 테마명 + 업체명
                    if await self._is_duplicate_escape_room(basic_info):
                        print(f"          🔄 중복 건너뛰기: {basic_info.name} - {basic_info.company} ({basic_info.region} > {basic_info.sub_region})")
                        continue
                    
                    # 상태 업데이트 (현재 처리중인 테마)
                    self.update_state(theme_name=basic_info.name)
                    
                    # 2단계: 카드 클릭하여 상세 페이지 이동
                    card_link = current_card.find_element(By.TAG_NAME, "a")
                    
                    # 스크롤해서 카드가 화면에 보이도록 하기
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card_link)
                    await asyncio.sleep(1)
                    
                    # JavaScript 클릭으로 overlay 무시
                    try:
                        self.driver.execute_script("arguments[0].click();", card_link)
                        # print(f"          🔗 JavaScript 클릭 성공")
                    except Exception as js_error:
                        print(f"          ⚠️ JavaScript 클릭 실패: {js_error}")
                        # 일반 클릭 시도
                        card_link.click()
                        print(f"          🔗 일반 클릭 성공")
                    
                    await asyncio.sleep(self._random_wait(3))
                    
                    # 3단계: 상세 페이지에서 추가 정보 수집
                    detailed_info = await self._extract_detailed_info_from_page()
                    
                    # 4단계: 기본 정보 + 상세 정보 결합
                    final_data = self._merge_escape_room_data(basic_info, detailed_info)
                    
                    # 🔍 DEBUG: 메모리에 저장 (페이지별 배치 저장용)
                    self.data.append(final_data)
                    processed_count += 1
                    
                    print(f"          ✅ 수집 완료: {final_data.name} - {final_data.company} ({final_data.price:,}원)")
                    print(f"             난이도: {final_data.difficulty_level}, 인원: {final_data.group_size_min}-{final_data.group_size_max}명")
                    print(f"             📦 메모리 저장: 총 {len(self.data)}개 누적")
                    
                    # 6단계: 뒤로가기
                    self.driver.back()
                    await asyncio.sleep(self._random_wait(3))
                    
                    # 페이지 로딩 완전히 대기 (더 강력하게)
                    try:
                        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "li")))
                        # 카드 목록이 실제로 로딩될 때까지 추가 대기
                        for wait_attempt in range(3):
                            try:
                                test_cards = self.driver.find_elements(By.XPATH, "//ul[contains(@class, 'CardList')]//li")
                                if len(test_cards) > 0:
                                    print(f"          ✅ 목록 페이지 복원 확인: {len(test_cards)}개 카드")
                                    break
                                else:
                                    await asyncio.sleep(1)
                            except:
                                await asyncio.sleep(1)
                    except Exception as e:
                        print(f"          ⚠️ 목록 페이지 복원 대기 오류: {e}")
                        
                except Exception as e:
                    print(f"        ⚠️ 카드 {i+1} 처리 오류: {e}")
                    # 오류 발생시 메인 목록으로 돌아가기 시도
                    try:
                        self.driver.back()
                        await asyncio.sleep(2)
                    except:
                        pass
                    continue
                    
        except Exception as e:
            print(f"      ❌ 페이지 카드 처리 오류: {e}")
            
        return processed_count
    
    async def _extract_basic_info_from_card(self, card, region_name: str, sub_region: str = "전체") -> EscapeRoomData:
        """카드에서 기본 정보 추출 (목록 페이지에서)"""
        try:
            # 기본값
            name = "방탈출 테마"
            company = "업체명 불명"
            theme = "기타"
            duration = 60
            price = 0
            rating = None
            image_url = ""
            source_url = ""
            
            # 테마명 추출
            try:
                name_elem = card.find_element(By.XPATH, ".//label[contains(@class, 'style__Label_SolidCardTitle-sc-iizpjd-8')]")
                name = name_elem.text.strip()
            except:
                pass
            
            # 업체명 추출 (| 앞부분)
            try:
                company_elem = card.find_element(By.XPATH, ".//p[contains(@class, 'style__P_SolidCardSubTitle-sc-iizpjd-9')]")
                company_text = company_elem.text.strip()
                if '|' in company_text:
                    company = company_text.split('|')[0].strip()
                else:
                    company = company_text
            except:
                pass
            
            # 장르와 시간 추출 (Chips에서)
            try:
                chips = card.find_elements(By.XPATH, ".//span[contains(@class, 'style__Chips-sc-1l4wlot-0') and contains(@class, 'jOvSeE')]")
                for chip in chips:
                    chip_text = chip.text.strip()
                    if chip_text.endswith('분'):
                        # 시간 정보
                        duration_match = re.search(r'(\d+)', chip_text)
                        if duration_match:
                            duration = int(duration_match.group(1))
                    elif len(chip_text) > 0 and not chip_text.endswith('분'):
                        # 장르 정보
                        theme = chip_text
            except:
                pass
            
            # 가격 추출
            try:
                price_elem = card.find_element(By.XPATH, ".//span[contains(@class, 'style__Span_Price-sc-iizpjd-10')]")
                price_text = price_elem.text.strip()
                if '원' in price_text and '정보 없음' not in price_text:
                    price_match = re.search(r'([\d,]+)', price_text.replace(',', ''))
                    if price_match:
                        price = int(price_match.group(1).replace(',', ''))
            except:
                pass
            
            # 평점 추출
            try:
                rating_elem = card.find_element(By.XPATH, ".//span[contains(@class, 'hhLGvj')]")
                rating_text = rating_elem.text.strip()
                rating_match = re.search(r'(\d+\.\d+)', rating_text)
                if rating_match:
                    rating = float(rating_match.group(1))
            except:
                pass
            
            # 이미지 URL 추출
            try:
                img_elem = card.find_element(By.XPATH, ".//img")
                image_url = img_elem.get_attribute('src') or ""
            except:
                pass
            
            # 링크 URL 추출
            try:
                link_elem = card.find_element(By.TAG_NAME, "a")
                source_url = link_elem.get_attribute('href') or ""
            except:
                pass
            
            return EscapeRoomData(
                name=name,
                region=region_name,
                sub_region=sub_region,  # 정확한 서브지역 사용
                theme=theme,
                duration=duration,
                price=price,
                company=company,
                rating=rating,
                image_url=image_url,
                source_url=source_url,
                description=""  # 상세 페이지에서 채울 예정
            )
            
        except Exception as e:
            print(f"          ⚠️ 기본 정보 추출 오류: {e}")
            return None
    
    async def _extract_detailed_info_from_page(self) -> Dict[str, Any]:
        """상세 페이지에서 추가 정보 추출"""
        detailed_info = {
            'difficulty_level': 3,
            'group_size_min': 2,
            'group_size_max': 4,
            'activity_level': 2,
            'description': "",
            'booking_url': ""
        }
        
        try:
            # SubInfoWrapper에서 정보 추출
            try:
                info_wrapper = self.driver.find_element(By.XPATH, "//ul[contains(@class, 'style__SubInfoWrapper-sc-1q1pihx-14')]")
                info_items = info_wrapper.find_elements(By.XPATH, ".//li[contains(@class, 'style__SubInfoList-sc-1q1pihx-16')]")
                
                for item in info_items:
                    try:
                        strong_elem = item.find_element(By.XPATH, ".//strong[contains(@class, 'style__SubInfoStrong-sc-1q1pihx-17')]")
                        span_elem = item.find_element(By.XPATH, ".//span[contains(@class, 'style__SubInfoLight-sc-1q1pihx-18')]")
                        
                        value = strong_elem.text.strip()
                        label = span_elem.text.strip()
                        
                        if label == "난이도":
                            if value == "쉬움":
                                detailed_info['difficulty_level'] = 2
                            elif value == "보통":
                                detailed_info['difficulty_level'] = 3
                            elif value == "어려움":
                                detailed_info['difficulty_level'] = 4
                                
                        elif label == "추천인원":
                            # "2인", "2~4인" 등 파싱
                            numbers = re.findall(r'(\d+)', value)
                            if numbers:
                                if len(numbers) >= 2:
                                    detailed_info['group_size_min'] = int(numbers[0])
                                    detailed_info['group_size_max'] = int(numbers[1])
                                else:
                                    people_count = int(numbers[0])
                                    detailed_info['group_size_min'] = people_count
                                    detailed_info['group_size_max'] = min(people_count + 2, 6)
                                    
                        elif label == "활동성":
                            if "거의 없음" in value:
                                detailed_info['activity_level'] = 1
                            elif "보통" in value:
                                detailed_info['activity_level'] = 2
                            elif "많음" in value or "활동적" in value:
                                detailed_info['activity_level'] = 3
                                
                    except:
                        continue
                        
            except:
                pass
            
            # 스토리 텍스트 추출
            try:
                story_elem = self.driver.find_element(By.XPATH, "//p[contains(@class, 'style__ThemeStoryContent-sc-x969cy-7')]")
                detailed_info['description'] = story_elem.text.strip()
            except:
                # 다른 가능한 스토리 셀렉터들
                story_selectors = [
                    "//section[contains(@class, 'ThemeStory')]//p",
                    "//*[contains(@class, 'story')]//p",
                    "//div[contains(@class, 'description')]//p"
                ]
                
                for selector in story_selectors:
                    try:
                        story_elem = self.driver.find_element(By.XPATH, selector)
                        story_text = story_elem.text.strip()
                        if len(story_text) > 20:  # 충분히 긴 텍스트만
                            detailed_info['description'] = story_text
                            print(f"        🔍 스토리 텍스트 추출: {story_text}")
                            break
                    except:
                        continue
            
            # 예약하러가기 URL 추출 (정확한 텍스트 매칭)
            try:
                print(f"        🔍 '예약하러가기' 버튼 찾는 중...")
                
                # "예약하러가기" 텍스트가 있는 요소 찾기 (버튼이든 링크든)
                booking_selectors = [
                    # 정확한 "예약하러가기" 텍스트만
                    "//*[text()='예약하러가기']",
                    "//*[contains(text(), '예약하러가기')]",
                    "//button[text()='예약하러가기']",
                    "//button[contains(text(), '예약하러가기')]",
                    "//a[text()='예약하러가기']",
                    "//a[contains(text(), '예약하러가기')]"
                ]
                
                found_booking_url = False
                
                for selector in booking_selectors:
                    try:
                        booking_elements = self.driver.find_elements(By.XPATH, selector)
                        if booking_elements:
                            print(f"        ✅ '예약하러가기' 요소 발견: {len(booking_elements)}개")
                            
                            for booking_elem in booking_elements:
                                try:
                                    # 1. 요소가 직접 <a> 태그인 경우
                                    if booking_elem.tag_name == 'a':
                                        booking_href = booking_elem.get_attribute('href')
                                        if booking_href:
                                            detailed_info['booking_url'] = booking_href
                                            print(f"        📋 예약 URL 수집 (직접): {booking_href}")
                                            found_booking_url = True
                                            break
                                    
                                    # 2. 요소 안에 <a> 태그가 있는 경우
                                    inner_links = booking_elem.find_elements(By.TAG_NAME, "a")
                                    if inner_links:
                                        for link in inner_links:
                                            booking_href = link.get_attribute('href')
                                            if booking_href:
                                                detailed_info['booking_url'] = booking_href
                                                print(f"        📋 예약 URL 수집 (내부): {booking_href}")
                                                found_booking_url = True
                                                break
                                        if found_booking_url:
                                            break
                                    
                                    # 3. 부모 또는 형제 요소에서 <a> 태그 찾기
                                    parent = booking_elem.find_element(By.XPATH, "./..")
                                    parent_links = parent.find_elements(By.TAG_NAME, "a")
                                    if parent_links:
                                        for link in parent_links:
                                            booking_href = link.get_attribute('href')
                                            if booking_href:
                                                detailed_info['booking_url'] = booking_href
                                                print(f"        📋 예약 URL 수집 (부모): {booking_href}")
                                                found_booking_url = True
                                                break
                                        if found_booking_url:
                                            break
                                            
                                except Exception as elem_error:
                                    print(f"        ⚠️ 요소 처리 오류: {elem_error}")
                                    continue
                            
                            if found_booking_url:
                                break
                                
                    except Exception as selector_error:
                        print(f"        ⚠️ 셀렉터 실패: {selector}")
                        continue
                
                if not found_booking_url:
                    print(f"        ❌ '예약하러가기' 버튼을 찾을 수 없음")
                            
            except Exception as e:
                print(f"        ❌ 예약 URL 추출 오류: {e}")
                        
        except Exception as e:
            print(f"          ⚠️ 상세 정보 추출 오류: {e}")
            
        return detailed_info
    
    def _merge_escape_room_data(self, basic_info: EscapeRoomData, detailed_info: Dict[str, Any]) -> EscapeRoomData:
        """기본 정보와 상세 정보를 결합"""
        # 기본 정보를 복사하고 상세 정보로 업데이트
        merged_data = EscapeRoomData(
            name=basic_info.name,
            region=basic_info.region,
            sub_region=basic_info.sub_region,
            theme=basic_info.theme,
            duration=basic_info.duration,
            price=basic_info.price,
            company=basic_info.company,
            rating=basic_info.rating,
            image_url=basic_info.image_url,
            source_url=basic_info.source_url,
            booking_url=detailed_info.get('booking_url', ""),
            description=detailed_info.get('description', f"{basic_info.name} - {basic_info.company}에서 운영하는 {basic_info.theme} 테마의 방탈출입니다."),
            difficulty_level=detailed_info.get('difficulty_level', 3),
            activity_level=detailed_info.get('activity_level', 2),
            group_size_min=detailed_info.get('group_size_min', 2),
            group_size_max=detailed_info.get('group_size_max', 4)
        )
        
        return merged_data
    
    def _log_unsaved_item(self, data: EscapeRoomData = None, reason: str = "알 수 없는 오류", 
                         region: str = None, sub_region: str = None, card_info: str = None):
        """저장되지 않은 아이템에 대한 상세 로깅 + Dead Letter JSON 저장"""
        try:
            if data:
                # EscapeRoomData가 있는 경우
                log_msg = f"❌ 저장 실패 - 방탈출: '{data.name}' | 업체: '{data.company}' | 지역: '{data.region} > {data.sub_region}' | 이유: {reason}"
                failed_item = {
                    "timestamp": datetime.now().isoformat(),
                    "error_type": "db_save_failed",
                    "reason": reason,
                    "data": data.to_dict(),
                    "metadata": {
                        "crawler_state": {
                            "region": self.state.current_region,
                            "sub_region": self.state.current_sub_region,
                            "page": self.state.current_page
                        }
                    }
                }
            else:
                # 기본 정보만 있는 경우
                log_msg = f"❌ 저장 실패 - 지역: '{region} > {sub_region}' | 카드: '{card_info}' | 이유: {reason}"
                failed_item = {
                    "timestamp": datetime.now().isoformat(),
                    "error_type": "extraction_failed",
                    "reason": reason,
                    "data": {
                        "region": region,
                        "sub_region": sub_region,
                        "card_info": card_info
                    },
                    "metadata": {
                        "crawler_state": {
                            "region": self.state.current_region,
                            "sub_region": self.state.current_sub_region,
                            "page": self.state.current_page
                        }
                    }
                }
            
            print(log_msg)
            
            # Dead Letter JSON 저장
            self._save_to_dead_letter_queue(failed_item)
            
            # 기존 로그 파일에도 기록
            try:
                from app.core.logger import logger
                logger.error(log_msg)
            except Exception as log_error:
                print(f"⚠️ 로그 파일 기록 실패: {log_error}")
            
        except Exception as e:
            print(f"⚠️ 로깅 오류: {e}")
    
    def _save_to_dead_letter_queue(self, failed_item: dict):
        """Dead Letter Queue에 실패한 아이템 저장"""
        try:
            # DLQ 디렉토리 생성
            dlq_dir = Path("data/dead_letters")
            dlq_dir.mkdir(parents=True, exist_ok=True)
            
            # 날짜별 파일 생성
            date_str = datetime.now().strftime("%Y%m%d")
            dlq_file = dlq_dir / f"crawler_failures_{date_str}.jsonl"
            
            # JSONL 형식으로 추가 (JSON Lines - 각 라인이 JSON 객체)
            with open(dlq_file, 'a', encoding='utf-8') as f:
                json.dump(failed_item, f, ensure_ascii=False)
                f.write('\n')
            
            print(f"💀 Dead Letter 저장: {dlq_file}")
            
        except Exception as e:
            print(f"⚠️ Dead Letter 저장 실패: {e}")
    
    async def _is_duplicate_escape_room(self, new_data: EscapeRoomData) -> bool:
        """DB에서 중복 방탈출 체크: region + sub_region + 테마명 + 업체명이 완벽하게 동일한지 확인"""
        try:
            query = """
            SELECT COUNT(*) FROM escape_rooms 
            WHERE region = $1 AND sub_region = $2 AND name = $3 AND company = $4
            """
            
            count = await self.db.fetchval(query, new_data.region, new_data.sub_region, new_data.name, new_data.company)
            
            return count > 0
            
        except Exception as e:
            print(f"⚠️ DB 중복 체크 오류: {e}")
            # DB 오류 시 메모리에서 체크 (fallback)
            for existing_data in self.data:
                if (existing_data.region == new_data.region and 
                    existing_data.sub_region == new_data.sub_region and
                    existing_data.name == new_data.name and
                    existing_data.company == new_data.company):
                    return True
            return False
    
    async def _save_to_database(self, data: EscapeRoomData) -> bool:
        """단일 방탈출 데이터를 DB에 저장"""
        try:
            # INSERT 쿼리 (ON CONFLICT 처리로 중복시 UPDATE)
            query = """
            INSERT INTO escape_rooms (
                name, region, sub_region, theme, duration_minutes, price_per_person,
                description, company, rating, image_url, source_url, booking_url,
                difficulty_level, activity_level, group_size_min, group_size_max
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
            )
            ON CONFLICT (name, region, sub_region, company) 
            DO UPDATE SET
                theme = EXCLUDED.theme,
                duration_minutes = EXCLUDED.duration_minutes,
                price_per_person = EXCLUDED.price_per_person,
                description = EXCLUDED.description,
                rating = EXCLUDED.rating,
                image_url = EXCLUDED.image_url,
                source_url = EXCLUDED.source_url,
                booking_url = EXCLUDED.booking_url,
                difficulty_level = EXCLUDED.difficulty_level,
                activity_level = EXCLUDED.activity_level,
                group_size_min = EXCLUDED.group_size_min,
                group_size_max = EXCLUDED.group_size_max,
                updated_at = CURRENT_TIMESTAMP
            """
            
            await self.db.execute(
                query,
                data.name, data.region, data.sub_region, data.theme,
                data.duration, data.price, data.description, data.company,
                data.rating, data.image_url, data.source_url, data.booking_url,
                data.difficulty_level, data.activity_level,
                data.group_size_min, data.group_size_max
            )
            return True
            
        except Exception as e:
            print(f"❌ DB 저장 오류: {e}")
            self._log_unsaved_item(data, f"DB 저장 오류: {str(e)}")
            return False
    
    async def _batch_save_to_database(self, data_list: List[EscapeRoomData]) -> int:
        """배치로 여러 방탈출 데이터를 DB에 저장"""
        if not data_list:
            return 0
            
        try:
            # INSERT 쿼리 (ON CONFLICT 처리로 중복시 UPDATE)
            query = """
            INSERT INTO escape_rooms (
                name, region, sub_region, theme, duration_minutes, price_per_person,
                description, company, rating, image_url, source_url, booking_url,
                difficulty_level, activity_level, group_size_min, group_size_max
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
            )
            ON CONFLICT (name, region, sub_region, company) 
            DO UPDATE SET
                theme = EXCLUDED.theme,
                duration_minutes = EXCLUDED.duration_minutes,
                price_per_person = EXCLUDED.price_per_person,
                description = EXCLUDED.description,
                rating = EXCLUDED.rating,
                image_url = EXCLUDED.image_url,
                source_url = EXCLUDED.source_url,
                booking_url = EXCLUDED.booking_url,
                difficulty_level = EXCLUDED.difficulty_level,
                activity_level = EXCLUDED.activity_level,
                group_size_min = EXCLUDED.group_size_min,
                group_size_max = EXCLUDED.group_size_max,
                updated_at = CURRENT_TIMESTAMP
            """
            
            # 데이터를 튜플 리스트로 변환
            batch_data = []
            for data in data_list:
                batch_data.append((
                    data.name, data.region, data.sub_region, data.theme,
                    data.duration, data.price, data.description, data.company,
                    data.rating, data.image_url, data.source_url, data.booking_url,
                    data.difficulty_level, data.activity_level,
                    data.group_size_min, data.group_size_max
                ))
            
            # 배치 실행
            await self.db.executemany(query, batch_data)
            
            print(f"✅ 배치 저장 완료: {len(data_list)}개 데이터")
            return len(data_list)
            
        except Exception as e:
            print(f"❌ 배치 저장 오류: {e}")
            print(f"   실패한 데이터 수: {len(data_list)}개")
            
            # 실패시 개별 저장으로 폴백
            print("🔄 개별 저장으로 폴백 시도...")
            success_count = 0
            for i, data in enumerate(data_list):
                if await self._save_to_database(data):
                    success_count += 1
                else:
                    print(f"   개별 저장 실패 {i+1}/{len(data_list)}: {data.name}")
                    self._log_unsaved_item(data, f"개별 저장 실패 (폴백 {i+1}/{len(data_list)})")
            
            print(f"🔄 폴백 완료: {success_count}/{len(data_list)}개 성공")
            return success_count
    
    async def _load_main_page(self):
        """메인 페이지 접속 및 로딩 확인"""
        print(f"🌐 메인 페이지 접속: {self.base_url}")
        self.driver.get(self.base_url)
        await asyncio.sleep(self._random_wait())
        
        # 페이지 로딩 확인
        print(f"📄 페이지 제목: {self.driver.title}")
        print(f"🔗 현재 URL: {self.driver.current_url}")
        
        # 페이지 로딩 완료 대기
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        print("✅ 페이지 로딩 완료")
    
    async def _find_region_navigation(self):
        """필터 → 지역 버튼을 클릭하여 지역 선택 화면 열기"""
        print("🔍 지역 필터 버튼 찾는 중...")
        
        try:
            # 1단계: "필터" 버튼 찾기
            filter_selectors = [
                "//button[contains(text(), '필터')]",
                "//button[contains(@class, 'filter')]",
                "//div[contains(text(), '필터')]//button",
                "//*[contains(text(), '필터')]"
            ]
            
            filter_clicked = False
            for selector in filter_selectors:
                try:
                    filter_btn = self.driver.find_element(By.XPATH, selector)
                    if filter_btn and filter_btn.is_displayed():
                        print(f"✅ 필터 버튼 클릭: {selector}")
                        filter_btn.click()
                        await asyncio.sleep(self._random_wait())
                        filter_clicked = True
                        break
                except:
                    continue
            
            if not filter_clicked:
                print("⚠️ 필터 버튼을 찾을 수 없습니다")
            
            # 2단계: "지역" 탭 버튼 찾기
            region_tab_selectors = [
                "//button[contains(text(), '지역')]",
                "//div[contains(@class, 'tab')]//button[contains(text(), '지역')]",
                "//span[contains(text(), '지역')]/..",
                "//*[contains(text(), '지역')]"
            ]
            
            for selector in region_tab_selectors:
                try:
                    region_tab = self.driver.find_element(By.XPATH, selector)
                    if region_tab and region_tab.is_displayed():
                        print(f"✅ 지역 탭 클릭: {selector}")
                        region_tab.click()
                        
                        # 지역 버튼들이 로딩될 때까지 충분히 대기
                        await asyncio.sleep(self._random_wait(3))
                        
                        # 지역 버튼들이 실제로 나타났는지 확인
                        for wait_time in [1, 2, 3]:
                            try:
                                region_buttons = self.driver.find_elements(By.XPATH, "//button[contains(text(), '서울') or contains(text(), '경기') or contains(text(), '부산')]")
                                if region_buttons:
                                    print(f"✅ 지역 버튼들 로딩 완료: {len(region_buttons)}개 발견")
                                    return region_tab
                                else:
                                    print(f"⏳ 지역 버튼 로딩 대기 중... ({wait_time}초)")
                                    await asyncio.sleep(wait_time)
                            except:
                                await asyncio.sleep(wait_time)
                        
                        print("⚠️ 지역 버튼들이 로딩되지 않았습니다")
                        return region_tab
                except:
                    continue
                    
            print("⚠️ 지역 탭을 찾을 수 없습니다")
            return None
            
        except Exception as e:
            print(f"❌ 지역 필터 찾기 오류: {e}")
            return None
    
    async def _click_region_button(self, region_name: str) -> bool:
        """지역 버튼 클릭 (이미지 기반 정확한 셀렉터)"""
        print(f"🎯 {region_name} 버튼 클릭 시도...")
        
        # 이미지에서 확인한 실제 구조에 맞는 셀렉터들
        region_selectors = [
            f"//button[contains(@class, 'FilterButton') and text()='{region_name}']",
            f"//button[contains(@class, 'SelectFilterButton') and text()='{region_name}']",
            f"//div[contains(@class, 'FilterTopLane')]//button[text()='{region_name}']",
            f"//button[text()='{region_name}']",
            f"//*[text()='{region_name}' and @role='button']",
            f"//*[contains(@class, 'button') and text()='{region_name}']"
        ]
        
        # 디버깅: 현재 페이지의 모든 버튼 확인
        try:
            all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
            print(f"    🔍 페이지에서 발견된 버튼들:")
            for i, btn in enumerate(all_buttons[:15]):  # 처음 15개만 확인
                try:
                    btn_text = btn.text.strip()
                    btn_class = btn.get_attribute('class')
                    if btn_text:
                        print(f"      버튼 {i+1}: '{btn_text}' (class: {btn_class})")
                except:
                    pass
        except:
            pass
        
        for selector in region_selectors:
            try:
                region_btn = self.driver.find_element(By.XPATH, selector)
                if region_btn and region_btn.is_displayed():
                    print(f"✅ {region_name} 버튼 발견: {selector}")
                    
                    # JavaScript를 통한 직접 클릭 (overlay 무시)
                    try:
                        self.driver.execute_script("arguments[0].click();", region_btn)
                        print(f"✅ {region_name} 버튼 JavaScript 클릭 성공")
                        await asyncio.sleep(self._random_wait())
                        return True
                    except Exception as js_error:
                        print(f"    ⚠️ JavaScript 클릭 실패: {js_error}")
                        
                        # 스크롤해서 버튼이 보이도록 한 후 다시 시도
                        try:
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", region_btn)
                            await asyncio.sleep(1)
                            self.driver.execute_script("arguments[0].click();", region_btn)
                            print(f"✅ {region_name} 버튼 스크롤 후 클릭 성공")
                            await asyncio.sleep(self._random_wait())
                            return True
                        except Exception as scroll_error:
                            print(f"    ⚠️ 스크롤 후 클릭도 실패: {scroll_error}")
                            continue
                            
            except Exception as e:
                print(f"    ⚠️ 셀렉터 실패: {selector} - {e}")
                continue
                
        print(f"❌ {region_name} 버튼을 찾을 수 없습니다")
        return False
    
    async def _click_subregion_button(self, sub_region: str) -> bool:
        """서브지역 버튼 클릭 - 메인 지역과 서브지역 구분"""
        print(f"    🎪 {sub_region} 서브지역 버튼 클릭...")
        
        # 현재 페이지의 모든 버튼 분석
        all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
        
        main_region_buttons = []
        subregion_candidates = []
        
        print(f"    🔍 '{sub_region}' 버튼 분석:")
        
        for i, btn in enumerate(all_buttons):
            try:
                btn_text = btn.text.strip()
                btn_class = btn.get_attribute('class') or ''
                
                if btn_text == sub_region:
                    # 메인 지역 버튼인지 확인 (SideTap 클래스)
                    is_main_region = 'SideTap' in btn_class
                    
                    if is_main_region:
                        main_region_buttons.append(btn)
                        print(f"      🚫 메인 지역 버튼 발견: '{btn_text}' (class: {btn_class[:50]})")
                    else:
                        subregion_candidates.append(btn)
                        print(f"      ✅ 서브지역 후보: '{btn_text}' (class: {btn_class[:50]})")
                        
            except:
                continue
        
        # 서브지역 후보가 있으면 우선 시도
        if subregion_candidates:
            print(f"    🎯 서브지역 후보 {len(subregion_candidates)}개 중 시도...")
            for i, sub_btn in enumerate(subregion_candidates):
                try:
                    if sub_btn.is_displayed() and sub_btn.is_enabled():
                        print(f"    🔗 서브지역 후보 {i+1} 클릭 시도...")
                        
                        # JavaScript 클릭으로 안전하게
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", sub_btn)
                        await asyncio.sleep(1)
                        self.driver.execute_script("arguments[0].click();", sub_btn)
                        await asyncio.sleep(self._random_wait())
                        
                        print(f"    ✅ {sub_region} 서브지역 선택 완료")
                        return True
                except Exception as e:
                    print(f"    ⚠️ 서브지역 후보 {i+1} 클릭 실패: {e}")
                    continue
        
        # 서브지역 후보가 없고 메인 지역 버튼만 있는 경우 (경고)
        if main_region_buttons and not subregion_candidates:
            print(f"    ⚠️ '{sub_region}'은 메인 지역 버튼만 발견됨 - 서브지역이 아닐 수 있음")
            # 메인 지역 버튼은 클릭하지 않음 (다른 지역으로 이동됨)
            return False
        
        # 폴백: 기존 방식으로 시도 (메인 지역 제외)
        subregion_selectors = [
            # 서브지역 전용 셀렉터들 (SideTap 클래스 제외)
            f"//button[text()='{sub_region}' and not(contains(@class, 'SideTap'))]",
            f"//div[contains(@class, 'subregion')]//button[text()='{sub_region}']",
            f"//div[contains(@class, 'SubRegion')]//button[text()='{sub_region}']",
            f"//*[contains(@class, 'sub')]//button[text()='{sub_region}']",
            # 마지막에만 일반 버튼 (위험)
            f"//button[text()='{sub_region}']"
        ]
        
        print(f"    🔄 폴백: 셀렉터로 서브지역 찾기...")
        for selector in subregion_selectors:
            try:
                sub_btns = self.driver.find_elements(By.XPATH, selector)
                
                for sub_btn in sub_btns:
                    if sub_btn and sub_btn.is_displayed():
                        # 한번 더 확인: SideTap 클래스가 아닌지
                        btn_class = sub_btn.get_attribute('class') or ''
                        if 'SideTap' in btn_class:
                            print(f"    🚫 SideTap 클래스 버튼 스킵: {selector}")
                            continue
                            
                        print(f"    🎯 셀렉터로 발견: {selector}")
                        self.driver.execute_script("arguments[0].click();", sub_btn)
                        await asyncio.sleep(self._random_wait())
                        print(f"    ✅ {sub_region} 서브지역 선택 완료")
                        return True
            except Exception as e:
                print(f"    ⚠️ 셀렉터 실패: {selector} - {e}")
                continue
                
        print(f"    ❌ {sub_region} 서브지역 버튼을 찾을 수 없습니다")
        return False
    
    async def _clear_current_subregion_filter(self):
        """현재 선택된 서브지역 필터 해제"""
        try:
            print("🧹 서브지역 필터 해제...")
            
            # 선택된 서브지역 태그의 X 버튼 클릭
            clear_buttons = self.driver.find_elements(By.XPATH, "//span[contains(text(), '×') or contains(text(), '✕')]")
            for btn in clear_buttons:
                try:
                    if btn.is_displayed():
                        btn.click()
                        await asyncio.sleep(1)
                        break
                except:
                    continue
                    
        except Exception as e:
            print(f"⚠️ 서브지역 필터 해제 오류: {e}")
    
    async def _go_to_next_page(self) -> bool:
        """다음 페이지로 이동 - 더 넓은 범위의 셀렉터와 디버깅 강화"""
        try:
            print("        🔍 페이지네이션 버튼 탐색 시작...")
            
            # 현재 페이지 URL과 카드 목록 저장 (마지막 페이지 감지용)
            current_url = self.driver.current_url
            current_cards = []
            try:
                # 현재 페이지의 카드 제목들 수집
                card_elements = self.driver.find_elements(By.XPATH, "//li[contains(@class, 'style__Li_SolidThemeCardContainer')]//label[contains(@class, 'style__Label_SolidCardTitle')]")
                current_cards = [elem.text.strip() for elem in card_elements if elem.text.strip()]
                print(f"        📋 현재 페이지 카드 수: {len(current_cards)}개")
                if current_cards:
                    print(f"        🏷️ 첫 번째 카드: '{current_cards[0]}'")
            except:
                pass
            
            # 먼저 현재 페이지 번호 확인
            current_page_num = 1
            try:
                # 활성화된 페이지 버튼 찾기
                active_page_buttons = self.driver.find_elements(By.XPATH, "//button[contains(@class, 'hDQLTC') or contains(@class, 'active') or @aria-current='page']")
                if active_page_buttons:
                    current_page_text = active_page_buttons[0].text.strip()
                    if current_page_text.isdigit():
                        current_page_num = int(current_page_text)
                        print(f"        📄 현재 페이지 번호: {current_page_num}")
            except:
                pass
            
            # 다음 페이지 번호 계산
            next_page_num = current_page_num + 1
            print(f"        🎯 목표: {current_page_num} → {next_page_num} 페이지로 이동")
            
            # 1순위: 다음 페이지 번호 버튼 직접 클릭 (여러 셀렉터 시도)
            next_page_found = False
            page_button_selectors = [
                f"//button[contains(@class, 'style__Button_PageButton') and text()='{next_page_num}']",
                f"//button[contains(@class, 'PageButton') and text()='{next_page_num}']",
                f"//button[text()='{next_page_num}' and contains(@class, 'Button')]",
                f"//button[text()='{next_page_num}']",
                f"//*[contains(@class, 'pagination')]//button[text()='{next_page_num}']",
                f"//*[contains(@class, 'paging')]//button[text()='{next_page_num}']"
            ]
            
            for selector in page_button_selectors:
                try:
                    next_page_button = self.driver.find_element(By.XPATH, selector)
                    if next_page_button and next_page_button.is_displayed() and next_page_button.is_enabled():
                        print(f"        🔢 {next_page_num}번 페이지 버튼 직접 클릭 ({selector})")
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", next_page_button)
                        await asyncio.sleep(1)
                        self.driver.execute_script("arguments[0].click();", next_page_button)
                        next_page_found = True
                    break
                except:
                    continue
                    
            if not next_page_found:
                print(f"        ⚠️ {next_page_num}번 페이지 버튼을 찾을 수 없음 - 화살표 버튼 시도")
            
            # 페이지네이션 영역 먼저 찾기
            pagination_area = None
            pagination_selectors = [
                "//*[contains(@class, 'pagination')]",
                "//*[contains(@class, 'Pagination')]",
                "//*[contains(@class, 'paging')]",
                "//*[contains(@class, 'page')]",
                "//nav",
                "//div[.//button[contains(text(), '1') or contains(text(), '2')]]"  # 숫자 버튼이 있는 영역
            ]
            
            for selector in pagination_selectors:
                try:
                    area = self.driver.find_element(By.XPATH, selector)
                    if area and area.is_displayed():
                        pagination_area = area
                        print(f"        📍 페이지네이션 영역 발견: {selector}")
                        break
                except:
                    continue
            
            # 디버깅: 모든 버튼 확인 (특히 페이지네이션 관련)
            print("        🔍 페이지의 모든 버튼 확인:")
            all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
            arrow_buttons = []
            
            # 특별히 우리가 찾는 정확한 버튼 확인
            target_buttons = self.driver.find_elements(By.XPATH, "//button[contains(@class, 'fokcDF')]")
            if target_buttons:
                print(f"        🎯 fokcDF 클래스 버튼 발견: {len(target_buttons)}개")
                for i, btn in enumerate(target_buttons):
                    try:
                        svg_elements = btn.find_elements(By.TAG_NAME, "svg")
                        has_chevron_right = any("chevron-right" in svg.get_attribute('class') for svg in svg_elements)
                        print(f"          fokcDF 버튼 {i+1}: chevron-right={has_chevron_right}, enabled={btn.is_enabled()}, displayed={btn.is_displayed()}")
                    except:
                        pass
            
            for i, btn in enumerate(all_buttons):
                try:
                    btn_text = btn.text.strip()
                    btn_class = btn.get_attribute('class') or ''
                    btn_aria = btn.get_attribute('aria-label') or ''
                    
                    # 정확한 클래스명 체크
                    is_target_arrow = ('fokcDF' in btn_class and 'style__Butto_ArrowButton' in btn_class)
                    has_chevron_svg = False
                    
                    try:
                        svg_elements = btn.find_elements(By.TAG_NAME, "svg")
                        has_chevron_svg = any("chevron-right" in svg.get_attribute('class') for svg in svg_elements)
                    except:
                        pass
                    
                    # 화살표나 다음 페이지 관련 버튼 찾기
                    if (is_target_arrow or has_chevron_svg or
                        any(keyword in btn_text.lower() for keyword in ['>', '다음', 'next']) or
                        any(keyword in btn_class.lower() for keyword in ['arrow', 'next', 'chevron']) or
                        any(keyword in btn_aria.lower() for keyword in ['next', 'arrow'])):
                        arrow_buttons.append(btn)
                        print(f"          🎯 화살표 후보 {len(arrow_buttons)}: '{btn_text}' | class: {btn_class} | chevron: {has_chevron_svg} | target: {is_target_arrow}")
                    elif i < 15:  # 처음 15개 버튼만 로깅 (줄임)
                        print(f"          버튼 {i+1}: '{btn_text}' (class: {btn_class[:40]})")
                except:
                    pass
            
            # 다음 페이지 화살표 버튼 찾기 - 오른쪽 화살표만!
            arrow_selectors = [
                # 가장 정확한 셀렉터: 오른쪽 chevron이 있는 버튼
                "//button[contains(@class, 'fokcDF') and .//svg[contains(@class, 'lucide-chevron-right')]]",
                "//button[contains(@class, 'style__Butto_ArrowButton-sc-4yy8xh-1') and .//svg[contains(@class, 'lucide-chevron-right')]]",
                
                # path가 "m9 18 6-6-6-6"인 오른쪽 화살표 (매우 구체적)
                "//button[.//svg//path[@d='m9 18 6-6-6-6']]",
                "//button//svg//path[@d='m9 18 6-6-6-6']/ancestor::button",
                
                # chevron-right SVG가 있는 버튼 (정확한 구조)
                "//button[.//svg[contains(@class, 'lucide-chevron-right')]]",
                "//button//svg[contains(@class, 'lucide-chevron-right')]/..",
                
                # 페이지네이션 컨테이너 내의 마지막 화살표 버튼 (오른쪽)
                "//*[contains(@class, 'PaginationContainer')]//button[contains(@class, 'ArrowButton')][last()]",
                "//*[contains(@class, 'cosjBH')]//button[contains(@class, 'fokcDF')][last()]",
                
                # 위치 기반 (페이지네이션 영역의 마지막 버튼)
                "//*[contains(@class, 'pagination')]//button[last()]",
                "//*[contains(@class, 'paging')]//button[last()]",
                "//nav//button[last()]",
                
                # 기존 백업 셀렉터들
                "//button[contains(@class, 'ArrowButton')]",
                "//button[contains(@class, 'Button_ArrowButton')]",
                "//button//svg[contains(@class, 'chevron-right')]/..",
                "//button[text()='>' or text()='→' or text()='▶']",
                "//button[contains(text(), '다음')]",
            ]
            
            # 수집된 화살표 후보 버튼들도 시도
            if arrow_buttons:
                print(f"        🎯 발견된 화살표 후보 버튼들 시도: {len(arrow_buttons)}개")
                
            # 페이지 번호 버튼이 성공하면 바로 이동 확인
            if next_page_found:
                print("        ✅ 페이지 번호 버튼 클릭 완료 - 이동 확인 중...")
            else:
                # 2순위: 페이지 번호 버튼이 없을 때만 화살표 버튼 시도
                print("        🏹 화살표 버튼으로 시도...")
                
                # 1. 셀렉터로 찾기
                for selector in arrow_selectors:
                    try:
                        next_btns = self.driver.find_elements(By.XPATH, selector)
                        for next_btn in next_btns:
                            if (next_btn and next_btn.is_displayed() and 
                                next_btn.is_enabled() and 
                                'disabled' not in (next_btn.get_attribute('class') or '').lower()):
                                
                                print(f"        ➡️ 화살표 버튼 발견 및 클릭: {selector}")
                                
                                # 스크롤하여 보이게 하기
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
                                await asyncio.sleep(1)
                                
                                # JavaScript 클릭으로 안전하게 실행
                                self.driver.execute_script("arguments[0].click();", next_btn)
                                next_page_found = True
                                break
                        if next_page_found:
                            break
                    except Exception as e:
                        print(f"        ⚠️ {selector} 실패: {e}")
                        continue
            
                # 2. 수집된 화살표 후보들 중 오른쪽 화살표만 시도
                if not next_page_found and arrow_buttons:
                    for i, btn in enumerate(arrow_buttons):
                        try:
                            if (btn.is_displayed() and btn.is_enabled() and 
                                'disabled' not in (btn.get_attribute('class') or '').lower()):
                                
                                # 오른쪽 화살표인지 확인
                                try:
                                    svg_elements = btn.find_elements(By.TAG_NAME, "svg")
                                    is_right_arrow = any("chevron-right" in svg.get_attribute('class') for svg in svg_elements)
                                    
                                    # 또는 path로 확인
                                    if not is_right_arrow:
                                        path_elements = btn.find_elements(By.XPATH, ".//path[@d='m9 18 6-6-6-6']")
                                        is_right_arrow = len(path_elements) > 0
                                    
                                    if not is_right_arrow:
                                        print(f"        ⬅️ 화살표 후보 {i+1}: 왼쪽 화살표 스킵")
                                        continue
                                        
                                except:
                                    # 확인 실패시 시도해보기
                                    pass
                                
                                print(f"        ➡️ 화살표 후보 버튼 {i+1} 클릭 시도 (오른쪽 화살표)")
                                
                                # 스크롤하여 보이게 하기
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                                await asyncio.sleep(1)
                                
                                # JavaScript 클릭
                                self.driver.execute_script("arguments[0].click();", btn)
                                next_page_found = True
                                break
                        except Exception as e:
                            print(f"        ⚠️ 화살표 후보 {i+1} 클릭 실패: {e}")
                            continue
                        
            if not next_page_found:
                print("        ⚠️ 다음 페이지 버튼을 찾을 수 없음 - 마지막 페이지로 판단")
                return False
            
            # 페이지 변경 대기
            await asyncio.sleep(self._random_wait(3))
            
            # 🎯 핵심: 페이지 이동 후 내용 비교로 마지막 페이지 감지
            # (백룸은 마지막 페이지에서도 다음 버튼이 있지만 내용이 변하지 않음)
            
            await asyncio.sleep(2)  # 페이지 로딩 완전 대기
            
            # 새 페이지의 카드 목록 추출
            try:
                new_card_elements = self.driver.find_elements(By.XPATH, "//li[contains(@class, 'style__Li_SolidThemeCardContainer')]//label[contains(@class, 'style__Label_SolidCardTitle')]")
                new_cards = [elem.text.strip() for elem in new_card_elements if elem.text.strip()]
                
                print(f"        📊 페이지 비교: 새 카드 {len(new_cards)}개 vs 이전 카드 {len(current_cards)}개")
                
                # 마지막 페이지 감지 조건들
                if not new_cards:
                    print(f"        🚩 새 페이지에 카드 없음 → 마지막 페이지")
                    return False
                elif not current_cards:
                    print(f"        ✅ 이전 카드 정보 없음 → 첫 페이지, 계속 진행")
                    return True
                
                # 카드 목록 완전 비교
                is_identical = new_cards == current_cards
                if is_identical:
                    print(f"        🚩 카드 목록 완전 동일 → 마지막 페이지 확정")
                    print(f"           첫 카드: '{new_cards[0] if new_cards else 'N/A'}'")
                    return False
            
                # 첫 번째 카드만 비교 (주요 지표)
                first_card_same = (new_cards[0] == current_cards[0]) if (new_cards and current_cards) else False
                if first_card_same:
                    # 첫 카드가 같으면 추가 확인
                    if len(new_cards) == len(current_cards):
                        # 개수도 같으면 마지막 페이지일 가능성 높음
                        same_count = sum(1 for i in range(min(3, len(new_cards), len(current_cards))) 
                                       if new_cards[i] == current_cards[i])
                        if same_count >= min(3, len(new_cards)):
                            print(f"        🚩 첫 3개 카드 동일 + 같은 개수 → 마지막 페이지")
                            return False
                        else:
                            print(f"        ⚠️ 첫 카드 같지만 일부 다름 → 계속 확인")
                    else:
                        print(f"        ⚠️ 첫 카드 같지만 개수 다름 ({len(new_cards)} vs {len(current_cards)}) → 계속")
                else:
                    print(f"        ✅ 새 페이지 확인: '{new_cards[0]}' (이전: '{current_cards[0]}')")
                
                return True
                    
            except Exception as e:
                print(f"        ❌ 카드 비교 실패: {e}")
                # 예외 발생시 URL 기반으로 판단
                new_url = self.driver.current_url
                if new_url != current_url:
                    print(f"        ✅ URL 변경됨 → 새 페이지로 간주")
                    return True
                else:
                    print(f"        🚩 URL 동일 → 마지막 페이지로 간주")
                    return False
                
        except Exception as e:
            print(f"        ❌ 페이지네이션 오류: {e}")
            traceback.print_exc()
            return False
    


async def main():
    """메인 실행 함수"""
    print("🎯 백룸 크롤러 시작!")
    
    # 크롤러 실행 
    crawler = BackroomCrawler(headless=False)  # 디버깅용으로 화면 보기
    data = await crawler.crawl_all_regions()
    
    # 통계 출력 
    print(f"\n📊 수집 통계:")
    print(f"  - 총 처리 세션: {crawler.state.total_collected}개")
    print(f"  - 완료된 지역: {len(crawler.state.completed_regions)}개")
    
    # 간단한 요약만
    total_sub_regions = sum(len(subs) for subs in crawler.state.completed_sub_regions.values())
    print(f"  - 완료된 서브지역: {total_sub_regions}개")
        
    print("💾 모든 데이터가 DB에 저장되었습니다!")
    print("🔄 다음 단계: python vector_generator.py 실행")

if __name__ == "__main__":
    asyncio.run(main())
