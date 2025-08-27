from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings - 현실적인 설정 관리"""
    
    # Database 
    database_url: str
    
    # PostgreSQL Settings
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433
    postgres_db: str = "escape_room_db"
    postgres_user: str = "escape_user"
    postgres_password: str 
    
    # Redis 
    redis_url: str
    
    # OpenAI 
    openai_api_key: str
    embedding_model: str = "text-embedding-ada-002"
    
    # Application 
    app_name: str = "Escape Room AI Chatbot"
    app_version: str = "1.0.0"
    debug: bool = True
    
    # Vector search 
    vector_search_limit: int = 10
    similarity_threshold: float = 0.7
    
    # Crawling Settings
    crawl_base_url: str = "https://www.test.com" 
    crawl_wait_time: int = 2
    crawl_page_timeout: int = 10
    crawl_batch_size: int = 10
    crawl_headless: bool = True
    
    # AWS S3 Settings (이미지 저장용)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-northeast-2"
    s3_bucket_name: str = "escape-room-images"
    s3_image_prefix: str = "posters/"
    
    # JWT Authentication
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 1
    
    class Config:
        # 우선순위: 1. 환경변수 2. .env 파일 3. 기본값
        env_file = ".env"
        case_sensitive = False


settings = Settings()
