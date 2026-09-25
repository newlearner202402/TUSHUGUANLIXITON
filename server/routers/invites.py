"""教师注册邀请码（仅管理员可生成/查看/作废）。"""
import secrets
import string
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import InviteCode, User
from ..schemas import InviteCreateIn, InviteOut, Page

router = APIRouter(prefix="/invites", tags=["邀请码"])

CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_LENGTH = 12


def _gen_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _status_of(invite: InviteCode, now: datetime) -> str:
    # 用 used_at 而不是 used_by 判断是否用过：账号被删除后 used_by 会置空，
    # 但 used_at 会保留，这样邀请码仍然算「已使用」，不会被重复使用。
    if invite.used_at is not None:
        return "used"
    if invite.expires_at is not None and invite.expires_at < now:
        return "expired"
    return "unused"


def _to_out(invite: InviteCode, now: datetime, used_names: dict[int, str]) -> InviteOut:
    return InviteOut(
        id=invite.id,
        code=invite.code,
        note=invite.note,
        expires_at=invite.expires_at,
        used_at=invite.used_at,
        created_at=invite.created_at,
        status=_status_of(invite, now),
        used_by_name=used_names.get(invite.used_by) if invite.used_by else None,
    )


@router.post("", response_model=list[InviteOut], summary="生成邀请码")
def create_invites(
    payload: InviteCreateIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """一次生成 N 个一码一用的邀请码，默认 7 天有效。"""
    now = datetime.now()
    expires_at = now + timedelta(days=payload.valid_days)
    note = (payload.note or "").strip() or None

    created = [
        InviteCode(
            code=_gen_code(),
            note=note,
            expires_at=expires_at,
            created_by=admin.id,
        )
        for _ in range(payload.count)
    ]
    db.add_all(created)
    db.commit()
    for invite in created:
        db.refresh(invite)
    return [_to_out(invite, now, {}) for invite in created]


@router.get("", response_model=Page, summary="邀请码列表")
def list_invites(
    status_filter: str | None = Query(
        default=None, alias="status", description="unused / used / expired"
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    now = datetime.now()
    stmt = select(InviteCode).order_by(InviteCode.id.desc())
    if status_filter == "unused":
        stmt = stmt.where(
            InviteCode.used_at.is_(None),
            (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at >= now),
        )
    elif status_filter == "used":
        stmt = stmt.where(InviteCode.used_at.is_not(None))
    elif status_filter == "expired":
        stmt = stmt.where(
            InviteCode.used_at.is_(None), InviteCode.expires_at < now
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(stmt.offset((page - 1) * size).limit(size)).scalars().all()
    )

    ids = [r.used_by for r in rows if r.used_by]
    used_names: dict[int, str] = {}
    if ids:
        used_names = {
            uid: name
            for uid, name in db.execute(
                select(User.id, User.real_name).where(User.id.in_(ids))
            ).all()
        }

    return Page(
        total=total,
        page=page,
        size=size,
        items=[_to_out(r, now, used_names) for r in rows],
    )


@router.delete("/{invite_id}", summary="作废邀请码")
def void_invite(
    invite_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    invite = db.get(InviteCode, invite_id)
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="邀请码不存在"
        )
    if invite.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邀请码已被使用，保留它是为了留痕，不能作废",
        )
    db.delete(invite)
    db.commit()
    return {"ok": True, "message": "邀请码已作废"}