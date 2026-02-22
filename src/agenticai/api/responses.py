from fastapi.responses import JSONResponse

from agenticai.api.schemas.tasks import ErrorResponse


def build_error_response(*, status_code: int, code: str, message: str) -> JSONResponse:
    """Build a strongly typed API error payload."""
    payload = ErrorResponse.model_validate(
        {
            "error": {
                "code": code,
                "message": message,
            }
        }
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
