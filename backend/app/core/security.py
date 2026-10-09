"""JWT authentication utilities — token creation, verification, password hashing."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt, JWTError
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db

from app.core.config import get_settings

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── Password hashing ──

def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password exceeds bcrypt's 72-byte limit")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


get_password_hash = hash_password


def verify_password(plain: str, hashed: str) -> bool:
    try:
        encoded = plain.encode("utf-8")
        return len(encoded) <= 72 and bcrypt.checkpw(encoded, hashed.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


# ── Token creation ──

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


# ── Token verification ──

def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """FastAPI dependency: extracts and validates the current user from the Bearer token."""
    payload = decode_token(token)
    if payload.get("type") != "access" or payload.get("purpose"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    from uuid import UUID
    from app.models.models import User

    try:
        user = await db.get(User, UUID(user_id))
    except ValueError:
        user = None
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


async def get_operator_user(user=Depends(get_current_user)):
    if user.account_role != "operator":
        raise HTTPException(403, "This action requires an operator account.")
    return user


def create_invitation(lead_id, operator_id):
    return create_access_token({"sub": str(lead_id), "operator_id": str(operator_id), "purpose": "client_invite"}, timedelta(days=7))


def read_invitation(token):
    payload = decode_token(token)
    if payload.get("purpose") != "client_invite":
        raise HTTPException(401, "Invalid client invitation.")
    return payload
