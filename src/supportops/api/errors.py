"""请求错误仅公开字段与类型，不回显正文或校验器内部数据。"""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    type: str


class ValidationErrorBody(BaseModel):
    code: Literal["REQUEST_VALIDATION_FAILED"] = "REQUEST_VALIDATION_FAILED"
    message: str = "请求参数不符合工单输入契约。"
    details: list[ErrorDetail]


class ErrorResponse(BaseModel):
    error: ValidationErrorBody


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


class ServiceErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class ServiceErrorResponse(BaseModel):
    error: ServiceErrorBody


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        body = ServiceErrorResponse(error=ServiceErrorBody(code=exc.code, message=exc.message))
        return JSONResponse(
            status_code=exc.status, content=body.model_dump(mode="json"), headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # loc 保留字段定位；input、ctx、模型报错原文不能进入公开响应。
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in item["loc"][1:]) or "body",
                type=item["type"],
            )
            for item in exc.errors()
        ]
        body = ErrorResponse(error=ValidationErrorBody(details=details))
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
