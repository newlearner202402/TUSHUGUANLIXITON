"""密码哈希与 JWT 签发/校验。"""
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_SECRET,
    REFRESH_TOKEN_EXPIRE_DAYS,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def _create_token(payload: dict[str, Any], expires_delta: timedelta, token_type: str) -> str:
    to_encode = payload.copy()
    to_encode.update(
        {
            "exp": datetime.now(timezone.utc) + expires_delta,
            "iat": datetime.now(timezone.utc),
            "type": token_type,
        }
    )
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_access_token(
    user_id: int, role: str, client: str = "web", sid: str | None = None
) -> str:
    return _create_token(
        {"sub": str(user_id), "role": role, "client": client, "sid": sid},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(
    user_id: int, role: str, client: str = "web", sid: str | None = None
) -> str:
    """sid 是这次登录的会话标识，服务端只认用户表里存的那一个，用于顶号。"""
    return _create_token(
        {"sub": str(user_id), "role": role, "client": client, "sid": sid},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
    )


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    if expected_type and payload.get("type") != expected_type:
        return None
    return payload
