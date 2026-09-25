"""用户管理（仅管理员）。"""
import csv
import io
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import BORROW_LIMIT
from ..database import get_db
from ..deps import require_admin
from ..models import BorrowRecord, InviteCode, User
from ..schemas import (
    BatchDeleteIn,
    BatchDeleteResult,
    BatchPasteIn,
    BatchRangeIn,
    BatchSkippedItem,
    ImportResult,
    Page,
    ResetPasswordIn,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)
from ..security import hash_password

router = APIRouter(prefix="/users", tags=["用户管理"])

# 能从管理页面分配的角色。管理员刻意不在其中：admin 账号在账号管理里整行不显示，
# 也不可禁用/删除/重置密码，所以不允许从任何管理接口新建管理员或把别人改成管理员
# （改成管理员会让那个账号立刻消失、再也管不了）。需要新管理员时直接改数据库。
ASSIGNABLE_ROLES = {"student", "teacher"}

# 范围生成学号的容量上限，避免一次事务过大
MAX_BATCH_RANGE = 2000


def _parse_max_borrow(raw: str | None) -> int | None:
    """CSV 里的 max_borrow 列，非法值返回 None，交给 _bulk_create 用角色默认值。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _bulk_create_once(db: Session, rows: list[dict]) -> ImportResult:
    """批量建号的一次尝试：逐行校验、跳过重复与非法项，最后统一提交。"""
    created = 0
    skipped = 0
    errors: list[str] = []
    seen: set[str] = set()

    # 一次查出所有已存在的账号，比逐行查库快得多，也把「先查后插」的窗口压到最小
    wanted = [(row.get("username") or "").strip() for row in rows]
    existing: set[str] = set()
    if wanted:
        existing = set(
            db.scalars(select(User.username).where(User.username.in_(wanted))).all()
        )

    # 先按行校验，通过的行攒起来，密码哈希留到后面一次性并发算
    pending: list[dict] = []
    for no, row in enumerate(rows, start=1):
        username = (row.get("username") or "").strip()
        password = (row.get("password") or "").strip()
        real_name = (row.get("real_name") or "").strip()
        role = (row.get("role") or "student").strip() or "student"

        if not username or not real_name:
            skipped += 1
            errors.append(f"第 {no} 行：账号或姓名为空，已跳过")
            continue
        if username in seen:
            skipped += 1
            errors.append(f"第 {no} 行：本批次内账号 {username} 重复，已跳过")
            continue
        if role not in ASSIGNABLE_ROLES:
            skipped += 1
            errors.append(f"第 {no} 行：角色 {role} 不可用（只能 student/teacher），已跳过")
            continue
        if len(password) < 6:
            skipped += 1
            errors.append(f"第 {no} 行：密码少于 6 位，已跳过")
            continue
        if username in existing:
            skipped += 1
            errors.append(f"第 {no} 行：账号 {username} 已存在，已跳过")
            continue

        max_borrow = row.get("max_borrow")
        if max_borrow is None:
            max_borrow = BORROW_LIMIT.get(role, 0)

        seen.add(username)
        pending.append(
            {
                "username": username,
                "password": password,
                "real_name": real_name,
                "role": role,
                "dept": (row.get("dept") or "").strip() or None,
                "phone": (row.get("phone") or "").strip() or None,
                "max_borrow": max_borrow,
            }
        )

    # bcrypt 每次哈希要几百毫秒，是整个批量建号里最慢的一步。
    # 它算哈希时会释放 GIL，所以开几个线程并行算能接近线性提速（实测 8 线程约 5 倍）。
    if pending:
        with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
            hashes = list(pool.map(lambda r: hash_password(r["password"]), pending))

        for item, password_hash in zip(pending, hashes):
            db.add(
                User(
                    username=item["username"],
                    password_hash=password_hash,
                    real_name=item["real_name"],
                    role=item["role"],
                    dept=item["dept"],
                    phone=item["phone"],
                    max_borrow=item["max_borrow"],
                )
            )
            created += 1

    db.commit()
    return ImportResult(created=created, skipped=skipped, errors=errors[:50])


def _bulk_create(db: Session, rows: list[dict]) -> ImportResult:
    """批量建号的公共逻辑，重复点击/并发时的同名账号不会把请求打成 500。

    rows 每项需含 username / password / real_name / role，可选 dept / phone / max_borrow。
    调用方负责保证 role 已经在允许范围内。

    查重和插入之间仍可能被别的请求抢先插入同名账号（唯一索引只在 commit 时才报错），
    这时整体回滚重来一次：抢先插入的那些账号这次能被查到，会被当作「已存在」跳过，
    剩下的照常创建，用户拿到的是一份正确的结果，而不是一个 500。
    """
    for attempt in range(2):
        try:
            return _bulk_create_once(db, rows)
        except IntegrityError:
            db.rollback()
            if attempt == 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="这批账号正在被其他操作创建，请稍后重试",
                )
    raise HTTPException(  # pragma: no cover - 上面必定 return 或 raise
        status_code=status.HTTP_409_CONFLICT, detail="创建账号失败，请重试"
    )


@router.get("", response_model=Page, summary="用户列表")
def list_users(
    q: str | None = None,
    role: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # 管理员账号在账号管理里不出现：看不到也就点不到禁用/删除/重置密码
    stmt = select(User).where(User.role != "admin")
    if q:
        kw = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(User.username.like(kw), User.real_name.like(kw), User.dept.like(kw))
        )
    if role:
        stmt = stmt.where(User.role == role)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(User.id.desc()).offset((page - 1) * size).limit(size)
        )
        .scalars()
        .all()
    )
    return Page(
        total=total,
        page=page,
        size=size,
        items=[UserOut.model_validate(u) for u in rows],
    )


@router.post("", response_model=UserOut, summary="新增用户")
def create_user(
    payload: UserCreateIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色只能是 {'/'.join(sorted(ASSIGNABLE_ROLES))} 之一",
        )
    exists = db.execute(
        select(User).where(User.username == payload.username.strip())
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"账号 {payload.username} 已存在",
        )

    user = User(
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        real_name=payload.real_name.strip(),
        role=payload.role,
        dept=payload.dept,
        phone=payload.phone,
        max_borrow=(
            payload.max_borrow
            if payload.max_borrow is not None
            else BORROW_LIMIT.get(payload.role, 0)
        ),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserOut, summary="修改用户")
def update_user(
    user_id: int,
    payload: UserUpdateIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色只能是 {'/'.join(sorted(ASSIGNABLE_ROLES))} 之一",
        )
    # 管理员账号不可禁用、也不可改角色（否则可以先降级成学生再删掉，绕过删除限制）
    if user.role == "admin" and ("role" in data or "status" in data):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="管理员账号不可禁用或改角色",
        )
    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password", summary="重置密码")
def reset_password(
    user_id: int,
    payload: ResetPasswordIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    # 管理员密码不允许由别人重置，只能本人在「修改密码」里用原密码改
    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="管理员密码不可重置，请由本人用「修改密码」修改",
        )
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": f"{user.real_name} 的密码已重置"}


def _delete_block_reason(db: Session, user: User) -> str | None:
    """返回不能删除的原因；可以删则返回 None。单个与批量删除共用。

    管理员账号一律不能删（调用方必然是管理员，这条也顺带挡住了删自己）。
    只有在借（未归还）的书才会拦住删除；已经归还的历史不拦，
    但会在 _purge_user 里跟着账号一起删掉。
    """
    if user.role == "admin":
        return "管理员账号不可删除"
    active = (
        db.scalar(
            select(func.count())
            .select_from(BorrowRecord)
            .where(BorrowRecord.user_id == user.id, BorrowRecord.return_at.is_(None))
        )
        or 0
    )
    if active:
        return f"还有 {active} 册书未归还，不能删除（可先改为「禁用」）"
    return None


def _purge_user(db: Session, user: User) -> None:
    """删账号：先清掉它的借阅历史、解绑邀请码，再删人。

    borrow_records.user_id 和 invite_codes.used_by 都没有级联，不先处理会被外键拦住。
    used_at 保留，所以邀请码仍然算「已使用」，不会被重复使用。
    """
    db.execute(
        delete(BorrowRecord)
        .where(BorrowRecord.user_id == user.id)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(InviteCode)
        .where(InviteCode.used_by == user.id)
        .values(used_by=None)
        .execution_options(synchronize_session=False)
    )
    db.delete(user)


@router.delete("/{user_id}", summary="删除用户（管理员账号、有未归还图书的不能删）")
def delete_user(
    user_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    reason = _delete_block_reason(db, user)
    if reason:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)
    _purge_user(db, user)
    db.commit()
    return {"ok": True, "message": "用户已删除"}


@router.post("/batch-delete", response_model=BatchDeleteResult, summary="批量删除用户")
def batch_delete_users(
    payload: BatchDeleteIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """部分成功并报告：管理员账号、有未归还图书的会被跳过。"""
    deleted = 0
    skipped: list[BatchSkippedItem] = []
    seen: set[int] = set()
    for user_id in payload.ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        user = db.get(User, user_id)
        if user is None:
            skipped.append(BatchSkippedItem(id=user_id, title=None, reason="用户不存在"))
            continue
        reason = _delete_block_reason(db, user)
        if reason:
            skipped.append(
                BatchSkippedItem(id=user_id, title=user.username, reason=reason)
            )
            continue
        _purge_user(db, user)
        deleted += 1
    db.commit()
    return BatchDeleteResult(deleted=deleted, skipped=skipped)


@router.post("/import", response_model=ImportResult, summary="CSV 批量导入用户")
def import_users(
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """CSV 表头：username,password,real_name,role,dept,phone,max_borrow"""
    raw = file.file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "username" not in reader.fieldnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV 表头必须包含 username,password,real_name,role 等列",
        )

    rows = [
        {
            "username": (row.get("username") or "").strip(),
            "password": (row.get("password") or "").strip(),
            "real_name": (row.get("real_name") or "").strip(),
            "role": (row.get("role") or "student").strip() or "student",
            "dept": row.get("dept"),
            "phone": row.get("phone"),
            "max_borrow": _parse_max_borrow(row.get("max_borrow")),
        }
        for row in reader
    ]
    return _bulk_create(db, rows)


@router.post("/batch-range", response_model=ImportResult, summary="按学号范围批量生成学生账号")
def batch_range_users(
    payload: BatchRangeIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """学号 = 4 位届数 + 2 位班号 + 2 位序号，如 20240101。

    默认密码与姓名都等于学号，姓名填纯数字（如 20240142），不含汉字。
    """
    total = payload.class_count * payload.per_class
    if total > MAX_BATCH_RANGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"一次最多生成 {MAX_BATCH_RANGE} 个账号，当前是 {total} 个",
        )

    rows = []
    for cls in range(1, payload.class_count + 1):
        for seq in range(1, payload.per_class + 1):
            username = f"{payload.year}{cls:02d}{seq:02d}"
            rows.append(
                {
                    "username": username,
                    "password": username,
                    "real_name": username,
                    "role": "student",
                    "dept": payload.dept,
                }
            )
    return _bulk_create(db, rows)


@router.post("/batch-paste", response_model=ImportResult, summary="粘贴名单批量建号")
def batch_paste_users(
    payload: BatchPasteIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """每行一条「学号,姓名」，姓名可省略（省略时用学号兜底）。默认密码等于学号。"""
    if payload.role != "student":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="粘贴名单只用于批量创建学生账号，教师请用邀请码注册",
        )

    rows = []
    for line in payload.text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        username = parts[0] if parts else ""
        real_name = parts[1] if len(parts) > 1 and parts[1] else username
        rows.append(
            {
                "username": username,
                "password": username,
                "real_name": real_name,
                "role": payload.role,
                "dept": payload.dept,
                "phone": parts[2] if len(parts) > 2 and parts[2] else None,
            }
        )

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="名单是空的，请先粘贴内容"
        )
    return _bulk_create(db, rows)
