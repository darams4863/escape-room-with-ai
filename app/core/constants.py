"""방탈출 경험 등급 및 추천 시스템 상수"""

# 방탈출 경험 등급 시스템
EXPERIENCE_LEVELS = {
    "방생아": {
        "min_count": 0,
        "max_count": 10,
        "description": "이제 막 시작한 단계. 룰도 어색하고, 힌트 없이는 힘든 경우가 많음.",
        "recommended_difficulty": [1, 2],
        "recommended_themes": ["로맨스", "감성", "코미디", "아이"],
        "tone": "친절한 가이드 톤",
        "example_message": "처음이라면 이 방이 딱 좋아요!"
    },
    "방린이": {
        "min_count": 11,
        "max_count": 30,
        "description": "기본 규칙을 알고, 웬만한 퍼즐은 익숙해진 단계.",
        "recommended_difficulty": [2, 3],
        "recommended_themes": ["추리", "판타지", "스릴러", "모험/탐험"],
        "tone": "동료 게이머 톤",
        "example_message": "이 정도는 가볍게 즐기실 수 있을 거예요"
    },
    "방소년": {
        "min_count": 31,
        "max_count": 50,
        "description": "다양한 장르 경험, 자신감과 몰입도가 올라가는 단계.",
        "recommended_difficulty": [3, 4],
        "recommended_themes": ["SF", "잠입", "스릴러", "모험/탐험"],
        "tone": "동료 게이머 톤",
        "example_message": "이 정도는 가볍게 즐기실 수 있을 거예요"
    },
    "방어른": {
        "min_count": 51,
        "max_count": 80,
        "description": "웬만한 장르는 다 경험해본 베테랑. 스토리·퀄리티 중심으로 방을 고름.",
        "recommended_difficulty": [4, 5],
        "recommended_themes": ["호러/공포", "드라마", "역사", "타임어택"],
        "tone": "존중하는 동료 톤",
        "example_message": "이건 베테랑도 만족할 만한 퀄리티예요"
    },
    "방신": {
        "min_count": 81,
        "max_count": 100,
        "description": "전국 방탈출 투어하는 수준. 새로운 방/신규 테마 위주로 움직임.",
        "recommended_difficulty": [5],
        "recommended_themes": ["고난도 추리", "잠입", "타임어택", "신작"],
        "tone": "존중 + 도전 욕구 자극",
        "example_message": "이건 고인물도 혀를 내두른다는 방이에요"
    },
    "방장로": {
        "min_count": 101,
        "max_count": 999,
        "description": "제작사/테마 트렌드까지 다 꿰고 있는 최상위 유저.",
        "recommended_difficulty": [5],
        "recommended_themes": ["전국 신작", "트렌드", "고난도"],
        "tone": "전문가 대우",
        "example_message": "방장로님께는 전국 신작 알림 서비스를 제공드려야겠네요!"
    }
}

# 경험 횟수별 등급 매핑 함수
def get_experience_level(count: int) -> str:
    """경험 횟수로 등급 반환"""
    for level, info in EXPERIENCE_LEVELS.items():
        if info["min_count"] <= count <= info["max_count"]:
            return level
    return "방생아"  # 기본값

# 등급별 추천 가중치
RECOMMENDATION_WEIGHTS = {
    "difficulty": 0.3,
    "theme": 0.25,
    "region": 0.2,
    "activity": 0.15,
    "price": 0.1
}