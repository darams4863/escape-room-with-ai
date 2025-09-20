import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings - 현실적인 설정 관리"""
    
    # Database 
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # PostgreSQL Settings (docker-compose.yml 기반)
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")  # 로컬에서 Docker 컨테이너 접근
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5433"))  # docker-compose.yml 포트 매핑
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "escape_room_db")  # docker-compose.yml 기본값
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "escape_user")  # docker-compose.yml 기본값
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "escape_password")  # docker-compose.yml에서 필수
    
    # Redis 
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # RabbitMQ
    RMQ_HOST: str = os.getenv("RMQ_HOST", "localhost")
    RMQ_PORT: int = int(os.getenv("RMQ_PORT", "5672"))
    RMQ_USERNAME: str = os.getenv("RMQ_USERNAME", "admin")
    RMQ_PASSWORD: str = os.getenv("RMQ_PASSWORD", "admin")
    RMQ_VHOST: str = os.getenv("RMQ_VHOST", "/")
    
    # OpenAI 
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002")
    
    # Application 
    APP_NAME: str = os.getenv("APP_NAME", "Escape Room AI Chatbot")
    APP_VERSION: str = os.getenv("APP_VERSION", "1.0.0")
    DEBUG: bool = bool(os.getenv("DEBUG", "true"))
    
    # Vector search 
    VECTOR_SEARCH_LIMIT: int = int(os.getenv("VECTOR_SEARCH_LIMIT", "10"))
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))
    
    # Crawling Settings
    CRAWL_BASE_URL: str = os.getenv("CRAWL_BASE_URL", "https://www.test.com")
    CRAWL_WAIT_TIME: int = int(os.getenv("CRAWL_WAIT_TIME", "2"))
    CRAWL_PAGE_TIMEOUT: int = int(os.getenv("CRAWL_PAGE_TIMEOUT", "10"))
    CRAWL_BATCH_SIZE: int = int(os.getenv("CRAWL_BATCH_SIZE", "10"))
    CRAWL_HEADLESS: bool = bool(os.getenv("CRAWL_HEADLESS", "true")) 
    
    # AWS S3 Settings (이미지 저장용)
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "ap-northeast-2")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "escape-room-images")
    S3_IMAGE_PREFIX: str = os.getenv("S3_IMAGE_PREFIX", "posters/")
    
    # JWT Authentication
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "your-secret-jwt-key-at-least-32-characters-long-here")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "1"))
    
    # NLP Prompt/Schema Versioning (선택사항)
    NLP_PROMPT_VERSION: str = os.getenv("NLP_PROMPT_VERSION", "intent.v1.2")
    NLP_SCHEMA_VERSION: str = os.getenv("NLP_SCHEMA_VERSION", "entities.v1.2")

    # Grafana
    GRAFANA_USERNAME: str = os.getenv("GRAFANA_USERNAME", "admin")
    GRAFANA_PASSWORD: str = os.getenv("GRAFANA_PASSWORD", "admin")       
    
    class Config:
        # 우선순위: 1. 환경변수 2. .env 파일 3. 기본값
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 추가 필드 허용


settings = Settings()
