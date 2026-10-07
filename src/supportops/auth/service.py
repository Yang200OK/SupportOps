"""随机 token 的身份来自数据库，客户端不能提供组织上下文。"""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from supportops.api.errors import ServiceError
from supportops.auth.contracts import LoginRequest, LoginResult, PrincipalView
from supportops.db.models import AuthSession, Organization, User
from supportops.db.runtime import request_session

PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
BEARER = HTTPBearer(auto_error=False)


def authentication_required() -> ServiceError:
    return ServiceError(
        401,
        "AUTHENTICATION_REQUIRED",
        "请提供有效的登录凭据。",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
) -> str:
    if credentials is None or len(credentials.credentials) > 128:
        raise authentication_required()
    return credentials.credentials


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    username: str
    organization_id: UUID
    organization_name: str
    session_id: UUID

    def view(self) -> PrincipalView:
        return PrincipalView(
            user_id=self.user_id,
            username=self.username,
            organization_id=self.organization_id,
            organization_name=self.organization_name,
        )


def login(session: Session, request: LoginRequest, ttl: int) -> LoginResult:
    row = session.execute(
        select(User, Organization.name).join(Organization).where(User.username == request.username)
    ).one_or_none()
    password_hash = row[0].password_hash if row is not None else DUMMY_HASH
    matches = PASSWORDS.verify(request.password.get_secret_value(), password_hash)
    if row is None or not matches or not row[0].active:
        raise authentication_required()
    user, organization_name = row
    token = secrets.token_urlsafe(32)
    now = session.scalar(select(func.now()))
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=sha256(token.encode()).hexdigest(),
            expires_at=now + timedelta(seconds=ttl),
        )
    )
    session.flush()
    return LoginResult(
        access_token=token,
        expires_in=ttl,
        principal=PrincipalView(
            user_id=user.id,
            username=user.username,
            organization_id=user.organization_id,
            organization_name=organization_name,
        ),
    )


def current_principal(
    token: Annotated[str, Depends(require_bearer)],
    session: Annotated[Session, Depends(request_session, scope="function")],
) -> Principal:
    row = session.execute(
        select(User, Organization.name, AuthSession.id)
        .join(Organization, Organization.id == User.organization_id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == sha256(token.encode()).hexdigest(),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > func.now(),
            User.active.is_(True),
        )
    ).one_or_none()
    if row is None:
        raise authentication_required()
    user, organization_name, session_id = row
    # true 表示仅对当前事务生效，提交 / 回滚之后不能遗留到连接池。
    session.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": str(user.organization_id)},
    )
    return Principal(user.id, user.username, user.organization_id, organization_name, session_id)


def logout(session: Session, principal: Principal) -> None:
    record = session.get(AuthSession, principal.session_id)
    record.revoked_at = session.scalar(select(func.now()))
