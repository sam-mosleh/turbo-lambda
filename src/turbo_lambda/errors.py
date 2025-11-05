from http import HTTPStatus
from typing import Any

import pydantic

from turbo_lambda import schemas


class ApplicationError(Exception):
    pass


class UnauthorizedError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("Unauthorized")


class GeneralError(ApplicationError):
    def __init__(
        self,
        error_type: str = "about:blank",
        status_code: HTTPStatus = HTTPStatus.BAD_REQUEST,
        title: str | None = None,
        detail: str = "General error",
        extensions: Any = None,
    ) -> None:
        self.error_type = error_type
        self.status_code = status_code
        self.title = status_code.description if title is None else title
        self.detail = detail
        self.extensions = extensions
        super().__init__(title)


class RequestValidationError(GeneralError):
    def __init__(self, error: pydantic.ValidationError) -> None:
        super().__init__(
            error_type="https://docs.pydantic.dev/errors/validation_errors/",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            title=error.title,
            detail=str(error),
            extensions=error.errors(),
        )


def general_error_to_gateway_response(
    error: GeneralError,
) -> schemas.ApiGatewaySerializedResponse:
    return schemas.ApiGatewayResponse(
        status_code=error.status_code,
        headers={"Content-Type": "application/problem+json"},
        body=schemas.HttpErrorResponse(
            type=error.error_type,
            status=error.status_code,
            title=error.title,
            detail=error.detail,
            extensions=error.extensions,
        ),
    ).model_dump()
