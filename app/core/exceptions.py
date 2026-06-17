"""Custom application exceptions."""

from typing import Any, Dict, Optional


class AppException(Exception):
    """Base application exception."""
    
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        # Backward-compatible with the (status_code, message, error_code) call order
        # used in some feature modules (e.g. purchasing_value): if the first arg is an
        # int status code and the second a string message, swap them so status_code
        # always ends up an int.
        if isinstance(message, int) and not isinstance(status_code, int):
            message, status_code = status_code, message
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(AppException):
    """Validation error - 422."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, 422, "VALIDATION_ERROR", details)


class NotFoundError(AppException):
    """Resource not found - 404."""
    
    def __init__(self, resource: str, identifier: Any):
        message = f"{resource} not found: {identifier}"
        super().__init__(message, 404, "NOT_FOUND_ERROR")


class UnauthorizedError(AppException):
    """Unauthorized access - 401."""
    
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, 401, "UNAUTHORIZED_ERROR")


class ForbiddenError(AppException):
    """Forbidden access - 403."""
    
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, 403, "FORBIDDEN_ERROR")


class ConflictError(AppException):
    """Resource conflict - 409."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, 409, "CONFLICT_ERROR", details)


class FileUploadError(AppException):
    """File upload error - 400."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, 400, "FILE_UPLOAD_ERROR", details)


class DataProcessingError(AppException):
    """Data processing error - 422."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, 422, "DATA_PROCESSING_ERROR", details)
