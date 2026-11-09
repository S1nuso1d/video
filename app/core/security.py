from datetime import datetime, timedelta, timezone
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user import UserAccount

_password_hash = PasswordHash.recommended()
_bearer = HTTPBearer(auto_error=False)
ALLOWED_ROLES = frozenset({"admin", "operator", "reviewer"})


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def create_access_token(user: UserAccount) -> tuple[str, int]:
    secret = settings.auth_secret_key
    if len(secret) < 32:
        raise RuntimeError("AUTH_SECRET_KEY must contain at least 32 characters")
    expires_in = settings.auth_access_token_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "iss": settings.auth_token_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "role": user.role,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token, expires_in


def decode_access_token(token: str) -> dict:
    secret = settings.auth_secret_key
    if len(secret) < 32:
        raise RuntimeError("AUTH_SECRET_KEY must contain at least 32 characters")
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer=settings.auth_token_issuer,
        options={"require": ["sub", "iss", "iat", "exp"]},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> UserAccount:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется действующая авторизация",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = claims.get("sub")
        if not isinstance(user_id, str):
            raise unauthorized
    except (jwt.InvalidTokenError, RuntimeError):
        raise unauthorized
    user = db.get(UserAccount, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_roles(*roles: str) -> Callable:
    allowed = frozenset(roles)
    if not allowed or not allowed.issubset(ALLOWED_ROLES):
        raise ValueError("Unknown or empty role set")

    def dependency(user: UserAccount = Depends(get_current_user)) -> UserAccount:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для выполнения операции",
            )
        return user

    return dependency
