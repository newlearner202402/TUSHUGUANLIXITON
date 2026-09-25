"""借书、还书、续借、借阅记录查询。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_staff
from ..models import Book, BookCopy, BorrowRecord, User
from ..schemas import (
    BorrowByBookIn,
    BorrowIn,
    BorrowRecordOut,
    BorrowResult,
    Page,
    ReturnIn,
)
from ..services import borrow as borrow_service
from ..services.borrow import to_out

router = APIRouter(prefix="/borrow", tags=["借还"])


def _query_records(
    db: Session,
    *,
    user_id: int | None = None,
    status_filter: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    size: int = 20,
):
    now = datetime.now()
    stmt = (
        select(BorrowRecord)
        .join(User, BorrowRecord.user_id == User.id)
        .join(Book, BorrowRecord.book_id == Book.id)
        .join(BookCopy, BorrowRecord.copy_id == BookCopy.id)
    )
    if user_id is not None:
        stmt = stmt.where(BorrowRecord.user_id == user_id)
    if status_filter == "borrowed":
        stmt = stmt.where(BorrowRecord.return_at.is_(None))
    elif status_filter == "returned":
        stmt = stmt.where(BorrowRecord.return_at.is_not(None))
    elif status_filter == "overdue":
        stmt = stmt.where(BorrowRecord.return_at.is_(None), BorrowRecord.due_at < now)
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                User.username.like(kw),
                User.real_name.like(kw),
                Book.title.like(kw),
                BookCopy.barcode.like(kw),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(BorrowRecord.id.desc()).offset((page - 1) * size).limit(size)
        )
        .scalars()
        .all()
    )
    return total, [to_out(r, now) for r in rows]


@router.post("", response_model=BorrowResult, summary="借书")
def do_borrow(
    payload: BorrowIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = borrow_service.borrow(
        db, user, payload.barcode, payload.username
    )
    return BorrowResult(
        ok=True,
        message=f"借阅成功：《{rec.book.title}》，请于 {rec.due_at:%Y-%m-%d} 前归还",
        record=to_out(rec),
    )


@router.post("/by-book", response_model=BorrowResult, summary="自助借书（借一册在馆副本）")
def do_borrow_by_book(
    payload: BorrowByBookIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """学生端「借书」按钮走这里：只知道书名，由服务端挑一册在馆的借出去。

    读者一律是操作者本人，所以不可能借到别人名下。
    """
    rec = borrow_service.borrow_by_book(db, user, payload.book_id)
    return BorrowResult(
        ok=True,
        message=f"借阅成功：《{rec.book.title}》，请于 {rec.due_at:%Y-%m-%d} 前归还",
        record=to_out(rec),
    )


@router.post("/return", response_model=BorrowResult, summary="还书")
def do_return(
    payload: ReturnIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = borrow_service.return_book(
        db, user, payload.barcode, payload.username
    )
    overdue = borrow_service.overdue_days_of(rec)
    msg = f"归还成功：《{rec.book.title}》"
    if overdue:
        msg += f"（已逾期 {overdue} 天，本次仅作提醒，不产生费用）"
    return BorrowResult(ok=True, message=msg, record=to_out(rec))


@router.post("/{record_id}/renew", response_model=BorrowRecordOut, summary="续借")
def do_renew(
    record_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = borrow_service.renew(db, user, record_id)
    return to_out(rec)


@router.get("/my", response_model=Page, summary="我的借阅记录")
def my_records(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total, items = _query_records(
        db, user_id=user.id, status_filter=status_filter, page=page, size=size
    )
    return Page(total=total, page=page, size=size, items=items)


@router.get("/records", response_model=Page, summary="全部借阅记录（馆员）")
def all_records(
    status_filter: str | None = Query(default=None, alias="status"),
    keyword: str | None = None,
    user_id: int | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    total, items = _query_records(
        db,
        user_id=user_id,
        status_filter=status_filter,
        keyword=keyword,
        page=page,
        size=size,
    )
    return Page(total=total, page=page, size=size, items=items)


@router.get("/overdue", response_model=Page, summary="逾期未还清单（馆员）")
def overdue_list(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    total, items = _query_records(db, status_filter="overdue", page=page, size=size)
    return Page(total=total, page=page, size=size, items=items)
