"""认证与权限依赖。"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或登录已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _UNAUTHORIZED
    payload = decode_token(credentials.credentials, expected_type="access")
    if payload is None:
        raise _UNAUTHORIZED
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise _UNAUTHORIZED
    if user.status != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
    # 顶号：同一个账号只有最近一次登录有效。登录时会把用户表里的 session_id 换掉，
    # 于是别处那个旧会话手上的 sid 就对不上了，下一跳请求就会被打回 401。
    # session_id 为空表示这个账号还没在顶号机制上线之后登录过，此时放行旧的 token。
    if user.session_id and payload.get("sid") != user.session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="你的账号已在其它地方登录，本页面已退出",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: str):
    """生成一个只允许指定角色通过的依赖。"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="权限不足，无法执行该操作"
            )
        return user

    return _dep


# 常用组合
require_staff = require_role("admin", "teacher")
require_admin = require_role("admin")
