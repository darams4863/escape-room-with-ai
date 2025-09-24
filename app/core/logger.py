import inspect
from pathlib import Path

from traceloggerx import set_logger

from .config import settings


class Logger:
    """제너럴 로거 - 모든 상황에서 사용 가능한 단일 로거"""
    
    _instance = None
    _base_logger = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._base_logger is None:
            self._setup_logger()
    
    def _setup_logger(self):
        """traceloggerx 기반 로거 설정"""
        # logs 디렉토리 생성
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # 기본 로거 생성
        self._base_logger = set_logger(
            "escape-room-ai",
            extra={
                "service": "escape_room_ai",
                "version": settings.APP_VERSION
            }
        )
    
    def _get_caller_info(self):
        """호출자 정보 추출 (성능 최적화)"""
        try:
            frame = inspect.currentframe().f_back.f_back
            file_path = frame.f_code.co_filename
            function_name = frame.f_code.co_name
            line_number = frame.f_lineno
            
            # 모듈 경로 추출
            path_obj = Path(file_path)
            if 'app' in path_obj.parts:
                app_index = path_obj.parts.index('app')
                module_parts = path_obj.parts[app_index:]
                module = '.'.join(module_parts).replace('.py', '')
            else:
                module = path_obj.stem
            
            return {
                "module": module,
                "function": function_name,
                "line": line_number
            }
        except:
            return {"module": "unknown", "function": "unknown", "line": 0}
    
    def _log(self, level: str, message: str, **kwargs):
        """통합 로깅 메서드"""
        # 호출자 정보 추출
        caller_info = self._get_caller_info()
        
        # 메시지 포맷팅 (파일명:함수명:라인번호)
        file_name = caller_info["module"].split('.')[-1]
        formatted_message = f"[{file_name}:{caller_info['function']}:{caller_info['line']}] {message}"
        
        # extra 정보 구성 (디버깅에 필요한 정보만)
        extra_info = {}
        
        # 사용자가 전달한 추가 정보만 포함
        if kwargs:
            extra_info.update(kwargs)
        
        # 에러 레벨인 경우 추가 정보 제공
        if level.lower() in ['error', 'critical']:
            # 간단한 traceback 정보
            if 'traceback' not in kwargs:
                try:
                    import traceback
                    tb_lines = traceback.format_stack()[-3:-1]
                    extra_info['traceback'] = [line.strip() for line in tb_lines if line.strip()]
                except:
                    pass
            
            # 에러 타입 정보 추가
            if 'error_type' not in kwargs:
                extra_info['error_type'] = 'unknown'
        
        # 로깅 실행
        getattr(self._base_logger, level.lower())(formatted_message, extra=extra_info)
    
    def info(self, message: str, **kwargs):
        """INFO 로그"""
        self._log("info", message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """DEBUG 로그"""
        self._log("debug", message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """WARNING 로그"""
        self._log("warning", message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """ERROR 로그"""
        self._log("error", message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """CRITICAL 로그"""
        self._log("critical", message, **kwargs)
    
    # 편의 메서드들
    def user_action(self, user_id: str, action: str, message: str, **kwargs):
        """사용자 행동 로깅"""
        self.info(message, user_id=user_id, action=action, **kwargs)
    
    def api_request(self, method: str, path: str, message: str, **kwargs):
        """API 요청 로깅"""
        self.info(message, method=method, path=path, **kwargs)
    
    def performance(self, operation: str, duration: float, message: str, **kwargs):
        """성능 로깅"""
        self.info(message, operation=operation, duration=duration, **kwargs)
    
    def business_event(self, event_type: str, message: str, **kwargs):
        """비즈니스 이벤트 로깅"""
        self.info(message, event_type=event_type, **kwargs)


# 전역 로거 인스턴스
logger = Logger()


# 편의 함수들 (하위 호환성 유지)
def get_user_logger(user_id: str):
    """사용자별 로거 (하위 호환성) - 실제로는 같은 logger 사용"""
    return logger

def get_trace_logger(trace_id: str):
    """트레이스별 로거 (하위 호환성) - 실제로는 같은 logger 사용"""
    return logger
