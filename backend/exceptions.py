import msgspec
from litestar import Request, Response
from litestar.status_codes import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR


class AppError(Exception):
    """Base for domain errors. Subclass per failure, set status_code + detail."""

    status_code: int = HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Internal Server Error"


class NotFoundError(AppError):
    status_code = HTTP_404_NOT_FOUND
    detail = "Resource not found"


class ProblemDetail(msgspec.Struct):
    """RFC 9457 problem detail body."""

    status: int
    detail: str
    type: str = "about:blank"


def app_error_handler(request: Request, exc: AppError) -> Response[ProblemDetail]:
    """Map any AppError subclass to a problem+json response."""
    content = ProblemDetail(
        detail=exc.detail,
        status=exc.status_code,
        type=exc.__class__.__name__,
    )
    return Response(
        content=content,
        status_code=exc.status_code,
        media_type="application/problem+json",
    )
