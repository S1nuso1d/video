"""Shared workspace access without per-user accounts or roles."""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer = HTTPBearer(auto_error=False)


def require_workspace_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Protect a shared deployment with one team key, when configured.

    An empty key keeps local development frictionless. Deployments exposed to
    other machines should always configure WORKSPACE_ACCESS_KEY.
    """
    expected = settings.workspace_access_key.strip()
    if not expected:
        return

    supplied = credentials.credentials if credentials is not None else ""
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется общий ключ рабочего пространства",
            headers={"WWW-Authenticate": "Bearer"},
        )
