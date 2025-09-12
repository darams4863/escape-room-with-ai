from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings - 현실적인 설정 관리"""
    
    # Database 
    DATABASE_URL: str = ""
    
    # PostgreSQL Settings
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5433
    POSTGRES_DB: str = "escape_room_db"
    POSTGRES_USER: str = "escape_user"
    POSTGRES_PASSWORD: str = ""
    
    # Redis 
    REDIS_URL: str = "redis://localhost:6379"
    
    # RabbitMQ
    RMQ_HOST: str = "localhost"
    RMQ_PORT: int = 5672
    RMQ_USERNAME: str = ""
    RMQ_PASSWORD: str = ""
    RMQ_VHOST: str = "/"
    
    # OpenAI 
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-ada-002"
    
    # Application 
    APP_NAME: str = "Escape Room AI Chatbot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    # Vector search 
    VECTOR_SEARCH_LIMIT: int = 10
    SIMILARITY_THRESHOLD: float = 0.7
    
    # Crawling Settings
    CRAWL_BASE_URL: str = "https://www.test.com"
    CRAWL_WAIT_TIME: int = 2
    CRAWL_PAGE_TIMEOUT: int = 10
    CRAWL_BATCH_SIZE: int = 10
    CRAWL_HEADLESS: bool = True
    
    # AWS S3 Settings (이미지 저장용)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-northeast-2"
    S3_BUCKET_NAME: str = "escape-room-images"
    S3_IMAGE_PREFIX: str = "posters/"
    
    # JWT Authentication
    JWT_SECRET_KEY: str = "your-secret-jwt-key-at-least-32-characters-long-here"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 1
    
    # NLP Prompt/Schema Versioning (선택사항)
    NLP_PROMPT_VERSION: str = "intent.v1.2"
    NLP_SCHEMA_VERSION: str = "entities.v1.2"

    # Grafana
    GRAFANA_USERNAME: str = ""
    GRAFANA_PASSWORD: str = ""       
    
    class Config:
        # 우선순위: 1. 환경변수 2. .env 파일 3. 기본값
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 추가 필드 허용


settings = Settings()
