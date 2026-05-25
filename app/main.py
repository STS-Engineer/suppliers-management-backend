"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""

    logger.info("Starting Supplier Management API")

    yield

    logger.info("Shutting down Supplier Management API")


app = FastAPI(
    title=settings.APP_NAME,
    description="Supplier Management & Evaluation Platform API",
    version="1.0.0",
    lifespan=lifespan,
)


def _error_response(
    *,
    status_code: int,
    message: str,
    error_code: str,
    details: dict | list | None = None,
):
    payload = {
        "status": "error",
        "message": message,
        "error_code": error_code,
    }
    if details:
        payload["details"] = details
    return JSONResponse(status_code=status_code, content=payload)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(
    request: Request,
    exc: AppException,
):
    """Handle application exceptions."""

    logger.warning(
        "Application exception | path=%s | error=%s",
        request.url.path,
        exc.message,
    )

    return _error_response(
        status_code=exc.status_code,
        message=exc.message,
        error_code=exc.error_code,
        details=exc.details,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    """Handle FastAPI request validation errors with a stable payload."""

    logger.warning(
        "Request validation error | path=%s | errors=%s",
        request.url.path,
        exc.errors(),
    )

    details = [
        {
            "field": ".".join(str(part) for part in error.get("loc", [])),
            "message": error.get("msg", "Invalid request"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]

    return _error_response(
        status_code=422,
        message="Some submitted fields are invalid. Please review the request and try again.",
        error_code="REQUEST_VALIDATION_ERROR",
        details=details,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request,
    exc: HTTPException,
):
    """Normalize HTTP errors into the API error contract."""

    detail = exc.detail
    message = "Request failed."
    error_code = "HTTP_ERROR"
    details = None

    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or message)
        error_code = str(detail.get("error_code") or error_code)
        details = detail.get("details")
    elif detail:
        message = str(detail)

    if exc.status_code >= 500:
        logger.error(
            "HTTP exception escalated as server error | path=%s | status=%s",
            request.url.path,
            exc.status_code,
        )
        message = "We couldn't complete this request right now. Please try again."
        error_code = "INTERNAL_SERVER_ERROR"
        details = None
    else:
        logger.warning(
            "HTTP exception | path=%s | status=%s | detail=%s",
            request.url.path,
            exc.status_code,
            detail,
        )

    return _error_response(
        status_code=exc.status_code,
        message=message,
        error_code=error_code,
        details=details,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    """Handle unexpected exceptions."""

    logger.exception(
        "Unhandled exception | path=%s",
        request.url.path,
    )

    return _error_response(
        status_code=500,
        message="We couldn't complete this request right now. Please try again.",
        error_code="INTERNAL_SERVER_ERROR",
    )


@app.get("/health", tags=["health"])
async def healthcheck() -> dict[str, str]:
    """Health check endpoint."""

    return {"status": "ok"}


# API routes
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
