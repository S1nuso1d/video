from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    hash_password,
    require_roles,
    verify_password,
)
from app.models.user import UserAccount
from app.schemas.auth import AdminUserCreate, LoginRequest, TokenResponse, UserResponse

router = APIRouter()


@router.post("/token", response_model=TokenResponse)
def issue_token(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(UserAccount).where(UserAccount.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись отключена",
        )
    try:
        token, expires_in = create_access_token(user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserResponse)
def get_me(
    user: UserAccount = Depends(require_roles("admin", "operator", "reviewer")),
) -> UserAccount:
    return user


@router.get("/reviewers", response_model=list[UserResponse])
def list_reviewers(
    db: Session = Depends(get_db),
    _admin: UserAccount = Depends(require_roles("admin")),
) -> list[UserAccount]:
    statement = (
        select(UserAccount)
        .where(UserAccount.role == "reviewer", UserAccount.is_active.is_(True))
        .order_by(UserAccount.email.asc())
    )
    return list(db.scalars(statement).all())


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    db: Session = Depends(get_db),
    _admin: UserAccount = Depends(require_roles("admin")),
) -> UserAccount:
    existing = db.scalar(select(UserAccount).where(UserAccount.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    user = UserAccount(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role.value,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
