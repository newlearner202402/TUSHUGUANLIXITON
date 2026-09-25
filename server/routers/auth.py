"""登录、注册、刷新、当前用户信息。"""
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BORROW_LIMIT
from ..database import get_db
from ..deps import get_current_user
from ..models import InviteCode, User
from ..schemas import (
    ChangePasswordIn,
    LoginIn,
    RefreshIn,
    RegisterIn,
    TokenOut,
    UserOut,
)
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["认证"])


def _issue(user: User, client: str) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user.id, user.role, client, user.session_id),
        refresh_token=create_refresh_token(user.id, user.role, client, user.session_id),
        role=user.role,
        real_name=user.real_name,
        username=user.username,
    )


@router.post("/login", response_model=TokenOut, summary="登录")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.username == payload.username.strip())
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码不正确"
        )
    if user.status != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员"
        )

    # 顶号：每次登录都换一个新的会话标识。旧的地方拿的还是上一轮的 sid，
    # 和库里对不上，它下一次请求（或刷新 token）就会被判为已失效。
    user.session_id = uuid4().hex
    db.commit()
    return _issue(user, payload.client)


@router.post("/register", response_model=TokenOut, summary="教师注册（凭邀请码）")
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    """公开接口。角色一律写死为 teacher，邀请码一码一用、过期即失效。"""
    username = payload.username.strip()
    exists = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"账号 {username} 已被占用"
        )

    # 加行锁：同一个邀请码被并发提交时，只会有一次成功
    invite = db.execute(
        select(InviteCode)
        .where(InviteCode.code == payload.invite_code.strip().upper())
        .with_for_update()
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="邀请码不存在，请核对后重试"
        )
    if invite.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该邀请码已被使用"
        )
    if invite.expires_at is not None and invite.expires_at < datetime.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该邀请码已过期，请向管理员索取新的"
        )

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        real_name=payload.real_name.strip(),
        role="teacher",
        dept=(payload.dept or "").strip() or None,
        phone=(payload.phone or "").strip() or None,
        max_borrow=BORROW_LIMIT["teacher"],
        session_id=uuid4().hex,
    )
    db.add(user)
    db.flush()

    invite.used_by = user.id
    invite.used_at = datetime.now()
    db.commit()
    db.refresh(user)
    return _issue(user, "web")


@router.post("/refresh", response_model=TokenOut, summary="用 refresh token 换新 token")
def refresh(payload: RefreshIn, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token, expected_type="refresh")
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录"
        )
    user = db.get(User, int(data["sub"]))
    if user is None or user.status != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    # 被顶下线的会话连刷新也不给续，否则旧页面能靠 refresh token 一直赖着不死
    if user.session_id and data.get("sid") != user.session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="你的账号已在其它地方登录，本次登录已失效",
        )
    return _issue(user, data.get("client", "web"))


@router.get("/me", response_model=UserOut, summary="当前登录用户")
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", summary="修改自己的密码")
def change_password(
    payload: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="原密码不正确"
        )
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": "密码已修改"}
