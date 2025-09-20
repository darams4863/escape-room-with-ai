"""
실무 스타일 모니터링 시스템
- 핵심 메트릭만 추적
- Prometheus 기반
- 사용하지 않는 기능 제거
"""

import time
import json
import psutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, asdict
from threading import Lock

# Prometheus 메트릭 수집
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# ===== 핵심 Prometheus 메트릭 정의 =====

# 비즈니스 메트릭
chat_messages_total = Counter(
    'chat_messages_total', 
    'Total number of chat messages',
    ['chat_type', 'success']
)

rag_usage_total = Counter(
    'rag_usage_total',
    'Total number of RAG usage',
    ['search_method', 'success']
)

# 사용자 관련 메트릭
user_registrations_total = Counter(
    'user_registrations_total',
    'Total number of user registrations'
)

user_logins_total = Counter(
    'user_logins_total',
    'Total number of user logins'
)

# 성능 메트릭 (핵심만 유지)
chat_response_time = Histogram(
    'chat_response_time_seconds',
    'Chat response time in seconds',
    ['chat_type']
)

# 비용 메트릭 (핵심만 유지)
openai_cost_total = Counter(
    'openai_cost_total_usd',
    'Total OpenAI API cost in USD',
    ['model']
)

# 앱 전용 메트릭
memory_usage_bytes = Gauge(
    'memory_usage_bytes',
    'Application memory usage in bytes'
)

cpu_usage_percent = Gauge(
    'cpu_usage_percent',
    'Application CPU usage percentage'
)

# API 호출 메트릭
api_calls_total = Counter(
    'api_calls_total',
    'Total number of API calls',
    ['service', 'endpoint', 'status_code']
)

api_response_time = Histogram(
    'api_response_time_seconds',
    'API response time in seconds',
    ['service', 'endpoint']
)

# 에러 메트릭
api_errors_total = Counter(
    'api_errors_total',
    'Total number of API errors',
    ['error_type', 'endpoint', 'method']
)

# ===== 핵심 메트릭 클래스 =====

@dataclass
class ChatMetric:
    """채팅 메트릭"""
    timestamp: str
    user_id: int
    session_id: str
    message_length: int
    response_time_ms: float
    chat_type: str
    used_rag: bool
    success: bool
    error_type: str | None = None

@dataclass
class APIMetric:
    """API 메트릭"""
    timestamp: str
    model: str
    operation: str
    input_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    response_time_ms: float
    success: bool
    error_type: str | None = None

class SimpleMetricsCollector:
    """단순화된 메트릭 수집기"""
    
    def __init__(self):
        self._lock = Lock()
        self.metrics_dir = Path("data/metrics")
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        
        # 메모리 내 메트릭 (최소한만)
        self.chat_metrics: List[ChatMetric] = []
        self.api_metrics: List[APIMetric] = []
        
        # 집계 데이터 (간단한 것만)
        self.daily_costs = {}
        self.error_counts = {}
    
    def track_chat_message(
        self, 
        user_id: int,
        session_id: str,
        message_length: int,
        response_time_ms: float,
        chat_type: str,
        used_rag: bool,
        success: bool = True,
        error_type: str | None = None
    ) -> ChatMetric:
        """채팅 메시지 추적"""
        metric = ChatMetric(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
            session_id=session_id,
            message_length=message_length,
            response_time_ms=response_time_ms,
            chat_type=chat_type,
            used_rag=used_rag,
            success=success,
            error_type=error_type
        )
        
        with self._lock:
            self.chat_metrics.append(metric)
            self._save_chat_metric(metric)
        
        # Prometheus 메트릭 수집 (고카디널리티 레이블 제거)
        chat_messages_total.labels(
            chat_type=chat_type,
            success=str(success)
        ).inc()
        
        chat_response_time.labels(chat_type=chat_type).observe(response_time_ms / 1000)
        
        return metric
    
    
    def track_error(self, error_type: str, endpoint: str, method: str, user_id: int | None = None):
        """에러 추적"""
        with self._lock:
            self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
        
        # Prometheus 메트릭 수집
        api_errors_total.labels(
            error_type=error_type,
            endpoint=endpoint,
            method=method
        ).inc()
    
    
    def track_infrastructure_cost(self, service: str, cost_usd: float):
        """인프라 비용 추적 (MVP에서 제거)"""
        pass  # MVP에서는 생략
    
    def set_memory_usage(self, bytes_used: int):
        """메모리 사용량 설정"""
        memory_usage_bytes.set(bytes_used)
    
    def set_cpu_usage(self, percent: float):
        """CPU 사용률 설정"""
        cpu_usage_percent.set(percent)
    
    
    def _calculate_cost(self, model: str, operation: str, tokens: int) -> float:
        """OpenAI 비용 계산"""
        pricing = {
            'text-embedding-ada-002': 0.0001,
            'text-embedding-3-small': 0.00002,
            'text-embedding-3-large': 0.00013,
            'gpt-4o-mini': 0.0006,  # $0.60 per 1M tokens
            'gpt-4': 0.03,
        }
        
        rate_per_1k = pricing.get(model, 0.0001)
        return (tokens / 1000) * rate_per_1k
    
    def _update_daily_costs(self, metric: APIMetric):
        """일일 비용 업데이트"""
        date_key = metric.timestamp[:10]
        self.daily_costs[date_key] = self.daily_costs.get(date_key, 0) + metric.estimated_cost_usd
    
    def _save_chat_metric(self, metric: ChatMetric):
        """채팅 메트릭 저장"""
        try:
            date_str = metric.timestamp[:10]
            metrics_file = self.metrics_dir / f"chat_metrics_{date_str}.jsonl"
            
            with open(metrics_file, 'a', encoding='utf-8') as f:
                json.dump(asdict(metric), f, ensure_ascii=False)
                f.write('\n')
        except Exception as e:
            print(f"⚠️ 채팅 메트릭 저장 실패: {e}")
    
    def _save_api_metric(self, metric: APIMetric):
        """API 메트릭 저장"""
        try:
            date_str = metric.timestamp[:10]
            metrics_file = self.metrics_dir / f"api_metrics_{date_str}.jsonl"
            
            with open(metrics_file, 'a', encoding='utf-8') as f:
                json.dump(asdict(metric), f, ensure_ascii=False)
                f.write('\n')
        except Exception as e:
            print(f"⚠️ API 메트릭 저장 실패: {e}")
    
    def get_daily_summary(self, date: str | None = None) -> Dict[str, Any]:
        """일일 요약"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        with self._lock:
            today_api_metrics = [m for m in self.api_metrics if m.timestamp.startswith(date)]
            today_chat_metrics = [m for m in self.chat_metrics if m.timestamp.startswith(date)]
            
            total_cost = sum(m.estimated_cost_usd for m in today_api_metrics)
            total_messages = len(today_chat_metrics)
            success_rate = (sum(1 for m in today_chat_metrics if m.success) / max(len(today_chat_metrics), 1)) * 100
            
            return {
                "date": date,
                "total_messages": total_messages,
                "total_cost_usd": round(total_cost, 6),
                "success_rate_percent": round(success_rate, 2),
                "api_calls": len(today_api_metrics)
            }

# 전역 메트릭 수집기
metrics = SimpleMetricsCollector()

# ===== 편의 함수들 =====

def track_chat_message(user_id: int, session_id: str, message_length: int, 
                      response_time_ms: float, chat_type: str, used_rag: bool, 
                      success: bool = True, error_type: str | None = None) -> ChatMetric:
    """채팅 메시지 추적"""
    return metrics.track_chat_message(
        user_id, session_id, message_length, response_time_ms, 
        chat_type, used_rag, success, error_type
    )


def track_error(error_type: str, endpoint: str, method: str, user_id: int | None = None):
    """에러 추적"""
    return metrics.track_error(error_type, endpoint, method, user_id)

def track_database_operation(operation: str, duration_ms: float, success: bool = True):
    """데이터베이스 작업 추적 (MVP에서 제거)"""
    pass  # MVP에서는 생략

def track_redis_operation(operation: str, duration_ms: float, success: bool = True):
    """Redis 작업 추적 (MVP에서 제거)"""
    pass  # MVP에서는 생략

def track_api_call(
    service: str, 
    endpoint: str, 
    status_code: int, 
    duration_seconds: float,
    model: str = None,
    cost_usd: float = None
):
    """통합된 API 호출 추적"""
    # 성공 여부 판단
    success = 200 <= status_code < 300
    
    # 비용 계산 (기본값: 내부 API는 비용 없음)
    if cost_usd is None:
        cost_usd = 0.0
    
    # Prometheus 메트릭 수집
    if service == "openai" and model and cost_usd > 0:
        # OpenAI API 호출 (비용 있음)
        openai_cost_total.labels(model=model).inc(cost_usd)
    
    # API 호출 수 메트릭
    api_calls_total.labels(service=service, endpoint=endpoint, status_code=str(status_code)).inc()
    
    # 응답 시간 메트릭
    api_response_time.labels(service=service, endpoint=endpoint).observe(duration_seconds)
    
    return {
        "service": service,
        "endpoint": endpoint,
        "status_code": status_code,
        "duration_seconds": duration_seconds,
        "success": success,
        "cost_usd": cost_usd
    }


def track_infrastructure_cost(service: str, cost_usd: float):
    """인프라 비용 추적"""
    return metrics.track_infrastructure_cost(service, cost_usd)

def set_memory_usage(bytes_used: int):
    """메모리 사용량 설정"""
    return metrics.set_memory_usage(bytes_used)


def track_user_registration():
    """사용자 등록 추적"""
    user_registrations_total.inc()

def track_user_login():
    """사용자 로그인 추적"""
    user_logins_total.inc()


def start_prometheus_server(port: int = 8000):
    """Prometheus 메트릭 서버 시작"""
    start_http_server(port)
    print(f"Prometheus metrics server started on port {port}")

def collect_system_metrics():
    """앱 전용 메트릭 수집"""
    try:
        import os
        process = psutil.Process(os.getpid())
        
        # 앱 메모리 사용량 (RSS - 실제 물리 메모리)
        app_memory = process.memory_info().rss
        memory_usage_bytes.set(app_memory)
        
        # 앱 CPU 사용률 (첫 번째 측정을 위해 0.1초 대기)
        app_cpu = process.cpu_percent(interval=0.1)
        cpu_usage_percent.set(app_cpu)
        
        print(f"📱 App - CPU: {app_cpu}%, Memory: {app_memory / (1024**2):.2f}MB")
        
    except ImportError:
        print("psutil not installed, app metrics disabled")
    except Exception as e:
        print(f"Error collecting app metrics: {e}")

# ===== 단순한 데코레이터들 =====

def track_performance(metric_name: str):
    """성능 추적 데코레이터"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = (time.time() - start_time) * 1000
                print(f"Performance - {metric_name}: {duration:.2f}ms")
                return result
            except Exception as e:
                duration = (time.time() - start_time) * 1000
                print(f"Performance - {metric_name} failed: {duration:.2f}ms, error: {e}")
                raise
        return wrapper
    return decorator