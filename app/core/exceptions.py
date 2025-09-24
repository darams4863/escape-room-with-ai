from typing import Any, Dict

from fastapi import HTTPException


class CustomError(Exception):
    """애플리케이션 에러 클래스 - 단일 클래스로 모든 에러 처리"""
    
    # 에러 코드와 메시지, HTTP 상태 코드를 한번에 정의
    ERRORS = {
        # 공통 에러 (200xxx) - 사용 중
        "INTERNAL_SERVER_ERROR": ("200001", "관리자에게 문의해주세요.", 500),
        "DB_ERROR": ("200004", "데이터베이스 처리 중 오류가 발생했습니다.", 503),
        
        # 공통 에러 (200xxx) - 사용 중
        "VALIDATION_ERROR": ("200003", "입력 데이터가 올바르지 않습니다.", 422),
        
        # 공통 에러 (200xxx) - 미사용 (주석 처리)
        # "NOT_FOUND": ("200002", "요청한 리소스를 찾을 수 없습니다.", 404),
        # "PERMISSION_DENIED": ("200005", "접근 권한이 없습니다.", 403),
        
        # 인증 에러 (201xxx) - 사용 중
        "USER_NOT_FOUND": ("201001", "사용자를 찾을 수 없습니다.", 404),
        "INVALID_CREDENTIALS": ("201003", "아이디 또는 비밀번호가 잘못되었습니다.", 401),
        "INACTIVE_USER": ("201004", "비활성화된 계정입니다.", 401),
        "INVALID_TOKEN": ("201005", "유효하지 않거나 만료된 토큰입니다.", 401),
        
        # 인증 에러 (201xxx) - 미사용 (주석 처리)
        # "USER_ALREADY_EXISTS": ("201002", "이미 존재하는 사용자입니다.", 409),
        
        # 채팅 에러 (202xxx) - 사용 중
        "CHATBOT_ERROR": ("202001", "챗봇 처리 중 오류가 발생했습니다.", 500),
        "SESSION_CREATION_FAILED": ("202004", "채팅 세션 생성에 실패했습니다.", 500),
        
        # 채팅 에러 (202xxx) - 사용 중
        "AI_API_CALL_ERROR": ("202002", "AI API 호출 중 오류가 발생했습니다.", 503),
        
        # 채팅 에러 (202xxx) - 미사용 (주석 처리)
        # "SESSION_NOT_FOUND": ("202003", "채팅 세션을 찾을 수 없습니다.", 404),
        
        # 방탈출 에러 (203xxx) - 사용 중
        "ROOM_NOT_FOUND": ("203001", "방탈출을 찾을 수 없습니다.", 404),
        
        # 방탈출 에러 (203xxx) - 미사용 (주석 처리)
        # "INVALID_DIFFICULTY": ("203002", "유효하지 않은 난이도입니다.", 422),
        
        # Rate Limiting 에러 (204xxx) - 미사용 (주석 처리)
        # "RATE_LIMIT_EXCEEDED": ("204001", "요청 한도를 초과했습니다.", 429),
        
        # 인증 에러 (201xxx) - 추가 (사용 중)
        "AUTH_ERROR": ("201006", "사용자 인증 중 오류가 발생했습니다.", 401),
    }
    
    def __init__(self, error_key: str, custom_message: str = None, **format_args):
        if error_key not in self.ERRORS:
            error_key = "INTERNAL_SERVER_ERROR"
        
        self.error_code, default_message, self.http_status = self.ERRORS[error_key]
        
        # 커스텀 메시지가 있으면 사용, 없으면 기본 메시지 사용
        if custom_message:
            self.message = custom_message.format(**format_args) if format_args else custom_message
        else:
            self.message = default_message.format(**format_args) if format_args else default_message
        
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환 (JSON 응답용)"""
        return {
            "status": "fail",
            "error_code": self.error_code,
            "message": self.message
        }
    
    def to_http_exception(self) -> HTTPException:
        """FastAPI HTTPException으로 변환"""
        headers = {"WWW-Authenticate": "Bearer"} if self.http_status == 401 else None
        
        return HTTPException(
            status_code=self.http_status,
            detail=self.to_dict(),
            headers=headers
        )
