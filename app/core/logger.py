import inspect
from pathlib import Path
from traceloggerx import set_logger
from .config import settings


class SmartLogger:
    """스마트 로거 - 자동으로 호출 위치 추적"""
    
    def __init__(self, base_logger):
        self.base_logger = base_logger
    
    def _get_caller_info(self, frame_offset=2):
        """호출자 정보 자동 추출 - 중복 제거 최적화"""
        try:
            frame = inspect.currentframe()
            for _ in range(frame_offset):
                frame = frame.f_back
            
            file_path = frame.f_code.co_filename
            function_name = frame.f_code.co_name
            line_number = frame.f_lineno
            
            file_name = Path(file_path).stem
            module_path = self._get_module_path(file_path)
            
            # 중복 정보 제거: file_path는 module에서 유추 가능하므로 제거
            # file은 module에서 마지막 부분과 동일하므로 module만 유지
            return {
                "module": module_path,
                "function": function_name,
                "line": line_number
            }
        except:
            return {}
    
    def _get_module_path(self, file_path: str) -> str:
        """파일 경로에서 모듈 경로 추출"""
        try:
            path_obj = Path(file_path)
            parts = path_obj.parts
            if 'app' in parts:
                app_index = parts.index('app')
                module_parts = parts[app_index:]
                return '.'.join(module_parts).replace('.py', '')
            return path_obj.stem
        except:
            return Path(file_path).stem
    
    def _log_with_location(self, level: str, message: str, **kwargs):
        """위치 정보와 함께 로깅"""
        caller_info = self._get_caller_info()
        
        # 모듈에서 파일명만 추출 (메시지용)
        module = caller_info.get('module', 'unknown')
        file_name = module.split('.')[-1] if module != 'unknown' else 'unknown'
        
        # 메시지에 간결한 위치 정보 추가
        formatted_message = f"[{file_name}:{caller_info.get('function', 'unknown')}:{caller_info.get('line', 0)}] {message}"
        
        # extra 정보 구성 (Python 로깅 예약 키워드 충돌 방지)
        extra_info = {}
        
        # caller_info를 안전한 키로 변환
        for key, value in caller_info.items():
            safe_key = f"caller_{key}" if key in ['module', 'function', 'line'] else key
            extra_info[safe_key] = value
        
        # kwargs 추가
        extra_info.update(kwargs)
        
        # 에러 레벨인 경우 자동으로 traceback 정보 추가 (선택적)
        if level.lower() in ['error', 'critical'] and 'include_traceback' not in kwargs:
            try:
                import traceback
                # 간결한 traceback (핵심 부분만)
                extra_info['traceback'] = traceback.format_stack()[-3:-1]
            except:
                pass
        
        # 로깅 실행
        getattr(self.base_logger, level.lower())(formatted_message, extra=extra_info)
    
    def info(self, message: str, **kwargs):
        """INFO 로그"""
        self._log_with_location("info", message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """DEBUG 로그"""
        self._log_with_location("debug", message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """WARNING 로그"""
        self._log_with_location("warning", message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """ERROR 로그"""
        self._log_with_location("error", message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """CRITICAL 로그"""
        self._log_with_location("critical", message, **kwargs)


class LoggerManager:
    """싱글톤 Logger 관리자 - 단일 로거로 모든 정보 추적"""
    
    _instance = None
    _logger = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LoggerManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._logger is None:
            self._setup_logger()
    
    def _setup_logger(self):
        """단일 traceloggerx Logger 설정"""
        # logs 디렉토리 생성
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # 단일 로거 생성
        base_logger = set_logger(
            "escape-room-ai",
            extra={
                "service": "escape_room_ai",
                "version": settings.app_version
            }
        )
        
        # 스마트 로거로 래핑
        self._logger = SmartLogger(base_logger)
    
    def get_logger(self):
        """스마트 로거 반환"""
        return self._logger
    
    def get_user_logger(self, user_id: str):
        """사용자별 로거"""
        base_logger = set_logger(
            "escape-room-ai",
            extra={
                "service": "escape_room_ai",
                "version": settings.app_version,
                "user_id": user_id
            }
        )
        return SmartLogger(base_logger)
    
    def get_trace_logger(self, trace_id: str):
        """트레이스별 로거"""
        base_logger = set_logger(
            "escape-room-ai",
            extra={
                "service": "escape_room_ai",
                "version": settings.app_version,
                "trace_id": trace_id
            }
        )
        return SmartLogger(base_logger)


# 전역 logger 관리자와 기본 로거 ======================================
logger_manager = LoggerManager()
logger = logger_manager.get_logger()


# 편의 함수들 ======================================================
def get_user_logger(user_id: str):
    """사용자별 로거 - 사용자 행동 추적 + 위치 정보"""
    return logger_manager.get_user_logger(user_id)

def get_trace_logger(trace_id: str):
    """요청별 추적 로거 - API 요청 추적 + 위치 정보"""
    return logger_manager.get_trace_logger(trace_id)
