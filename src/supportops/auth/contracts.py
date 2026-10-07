"""身份输出没有口令摘要或会话 token 摘要。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: Annotated[
        str, StringConstraints(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_.-]+$")
    ]
    password: SecretStr

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("口令长度须为 1～128 字符。")
        return value


class PrincipalView(BaseModel):
    user_id: UUID
    username: str
    organization_id: UUID
    organization_name: str


class LoginResult(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    principal: PrincipalView
