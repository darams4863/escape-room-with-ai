"""
범용 모니터링 시스템 (MVP → 운영 확장 가능)
- API 호출 추적 (OpenAI, 기타 외부 API)
- 성능 모니터링 (응답시간, 성공률)
- 비용 추적 (토큰 기반)
- 파일 기반 저장 (나중에 DB/Grafana 연동 가능)
"""

import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, asdict
from threading import Lock
from collections import defaultdict

@dataclass
class APIUsageMetric:
    """OpenAI API 사용량 메트릭"""
    timestamp: str
    model: str
    operation: str  # "embedding", "completion"
    input_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    response_time_ms: float
    success: bool
    error_type: str | None = None
    batch_size: int = 1
    
@dataclass  
class VectorizationMetric:
    """벡터화 프로세스 메트릭"""
    timestamp: str
    total_items: int
    successful_items: int
    failed_items: int
    batch_count: int
    total_duration_seconds: float
    avg_response_time_ms: float
    total_estimated_cost_usd: float
    error_breakdown: Dict[str, int]

class MetricsCollector:
    """실무 메트릭 수집기 (Thread-Safe)"""
    
    def __init__(self):
        self._lock = Lock()
        self.metrics_dir = Path("data/metrics")
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        
        # 메모리 내 메트릭 (실시간 조회용)
        self.api_metrics: List[APIUsageMetric] = []
        self.vectorization_sessions: List[VectorizationMetric] = []
        
        # 집계 데이터 (대시보드용)
        self.daily_costs = defaultdict(float)
        self.model_usage = defaultdict(int)
        self.error_counts = defaultdict(int)
        
    def track_api_call(self, 
                      model: str,
                      operation: str,
                      input_tokens: int,
                      total_tokens: int,
                      response_time_ms: float,
                      success: bool = True,
                      error_type: str | None = None,
                      batch_size: int = 1) -> APIUsageMetric:
        """OpenAI API 호출 추적"""
        
        # 토큰 기반 비용 계산 (2024년 기준)
        cost = self._calculate_cost(model, operation, total_tokens)
        
        metric = APIUsageMetric(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=cost,
            response_time_ms=response_time_ms,
            success=success,
            error_type=error_type,
            batch_size=batch_size
        )
        
        with self._lock:
            self.api_metrics.append(metric)
            self._update_aggregates(metric)
            self._save_api_metric(metric)
        
        return metric
    
    def start_vectorization_session(self, total_items: int) -> 'VectorizationSession':
        """벡터화 세션 시작"""
        return VectorizationSession(self, total_items)
    
    def _calculate_cost(self, model: str, operation: str, tokens: int) -> float:
        """실제 OpenAI 가격표 기반 비용 계산"""
        # 2024년 1월 기준 가격 (1M 토큰당 USD)
        pricing = {
            'text-embedding-ada-002': 0.0001,      # $0.0001/1K tokens
            'text-embedding-3-small': 0.00002,     # $0.00002/1K tokens  
            'text-embedding-3-large': 0.00013,     # $0.00013/1K tokens
        }
        
        rate_per_1k = pricing.get(model, 0.0001)  # 기본값
        return (tokens / 1000) * rate_per_1k
    
    def _update_aggregates(self, metric: APIUsageMetric):
        """집계 데이터 업데이트 (Thread-Safe 내부 호출)"""
        date_key = metric.timestamp[:10]  # YYYY-MM-DD
        
        self.daily_costs[date_key] += metric.estimated_cost_usd
        self.model_usage[metric.model] += metric.batch_size
        
        if not metric.success and metric.error_type:
            self.error_counts[metric.error_type] += 1
    
    def _save_api_metric(self, metric: APIUsageMetric):
        """API 메트릭을 파일에 저장 (JSONL)"""
        try:
            date_str = metric.timestamp[:10]
            metrics_file = self.metrics_dir / f"api_usage_{date_str}.jsonl"
            
            with open(metrics_file, 'a', encoding='utf-8') as f:
                json.dump(asdict(metric), f, ensure_ascii=False)
                f.write('\n')
                
        except Exception as e:
            print(f"⚠️ 메트릭 저장 실패: {e}")
    
    def get_daily_summary(self, date: str | None = None) -> Dict[str, Any]:
        """일일 사용량 요약"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
            
        with self._lock:
            today_metrics = [m for m in self.api_metrics if m.timestamp.startswith(date)]
            
            total_cost = sum(m.estimated_cost_usd for m in today_metrics)
            total_tokens = sum(m.total_tokens for m in today_metrics)
            success_rate = (sum(1 for m in today_metrics if m.success) / 
                          max(len(today_metrics), 1)) * 100
            
            return {
                "date": date,
                "total_calls": len(today_metrics),
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 6),
                "success_rate_percent": round(success_rate, 2),
                "model_breakdown": self._get_model_breakdown(today_metrics),
                "avg_response_time_ms": self._get_avg_response_time(today_metrics)
            }
    
    def _get_model_breakdown(self, metrics: List[APIUsageMetric]) -> Dict[str, Dict]:
        """모델별 사용량 분석"""
        breakdown = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
        
        for metric in metrics:
            breakdown[metric.model]["calls"] += 1
            breakdown[metric.model]["tokens"] += metric.total_tokens
            breakdown[metric.model]["cost"] += metric.estimated_cost_usd
            
        return dict(breakdown)
    
    def _get_avg_response_time(self, metrics: List[APIUsageMetric]) -> float:
        """평균 응답 시간 계산"""
        if not metrics:
            return 0.0
        return sum(m.response_time_ms for m in metrics) / len(metrics)
    
    def export_prometheus_metrics(self) -> str:
        """Prometheus 형식으로 메트릭 내보내기"""
        lines = [
            "# HELP openai_api_calls_total Total OpenAI API calls",
            "# TYPE openai_api_calls_total counter",
            "# HELP openai_api_cost_usd_total Total OpenAI API cost in USD", 
            "# TYPE openai_api_cost_usd_total counter",
            "# HELP openai_api_tokens_total Total tokens used",
            "# TYPE openai_api_tokens_total counter"
        ]
        
        with self._lock:
            # 모델별 집계
            for model, usage in self.model_usage.items():
                lines.append(f'openai_api_calls_total{{model="{model}"}} {usage}')
            
            # 비용 집계  
            total_cost = sum(self.daily_costs.values())
            lines.append(f'openai_api_cost_usd_total {total_cost}')
            
            # 토큰 집계
            total_tokens = sum(m.total_tokens for m in self.api_metrics)
            lines.append(f'openai_api_tokens_total {total_tokens}')
        
        return '\n'.join(lines)

class VectorizationSession:
    """벡터화 세션 트래커 (컨텍스트 매니저)"""
    
    def __init__(self, collector: MetricsCollector, total_items: int):
        self.collector = collector
        self.total_items = total_items
        self.successful_items = 0
        self.failed_items = 0
        self.batch_count = 0
        self.start_time = time.time()
        self.api_calls = []
        self.error_breakdown = defaultdict(int)
    
    def record_batch(self, success_count: int, failure_count: int):
        """배치 결과 기록"""
        self.successful_items += success_count
        self.failed_items += failure_count
        self.batch_count += 1
    
    def record_error(self, error_type: str, count: int = 1):
        """에러 기록"""
        self.error_breakdown[error_type] += count
    
    def add_api_call(self, metric: APIUsageMetric):
        """API 호출 메트릭 추가"""
        self.api_calls.append(metric)
    
    def finish(self) -> VectorizationMetric:
        """세션 종료 및 메트릭 생성"""
        duration = time.time() - self.start_time
        
        total_cost = sum(call.estimated_cost_usd for call in self.api_calls)
        avg_response_time = (sum(call.response_time_ms for call in self.api_calls) / 
                           max(len(self.api_calls), 1))
        
        metric = VectorizationMetric(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_items=self.total_items,
            successful_items=self.successful_items,
            failed_items=self.failed_items,
            batch_count=self.batch_count,
            total_duration_seconds=duration,
            avg_response_time_ms=avg_response_time,
            total_estimated_cost_usd=total_cost,
            error_breakdown=dict(self.error_breakdown)
        )
        
        with self.collector._lock:
            self.collector.vectorization_sessions.append(metric)
            self.collector._save_vectorization_metric(metric)
        
        return metric
    
    def _save_vectorization_metric(self, metric: VectorizationMetric):
        """벡터화 메트릭 저장"""
        try:
            date_str = metric.timestamp[:10]
            metrics_file = self.collector.metrics_dir / f"vectorization_{date_str}.jsonl"
            
            with open(metrics_file, 'a', encoding='utf-8') as f:
                json.dump(asdict(metric), f, ensure_ascii=False)
                f.write('\n')
                
        except Exception as e:
            print(f"⚠️ 벡터화 메트릭 저장 실패: {e}")

# 전역 메트릭 수집기
metrics_collector = MetricsCollector()

# 편의 함수들
def track_openai_call(
    model: str, 
    operation: str, 
    input_tokens: int, 
    total_tokens: int, 
    response_time_ms: float, 
    success: bool = True, 
    error_type: str | None = None,
    batch_size: int = 1) -> APIUsageMetric:
    """OpenAI API 호출 추적 (전역 함수)"""
    return metrics_collector.track_api_call(
        model, operation, input_tokens, total_tokens, 
        response_time_ms, success, error_type, batch_size
    )

def get_daily_usage(date: str | None = None) -> Dict[str, Any]:
    """일일 사용량 조회"""
    return metrics_collector.get_daily_summary(date)

def export_metrics_for_grafana() -> str:
    """Grafana용 메트릭 내보내기"""
    return metrics_collector.export_prometheus_metrics()
