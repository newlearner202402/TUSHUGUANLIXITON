"""借书 / 还书 / 续借的事务核心。并发安全全部集中在这里。

关键点：所有「检查 + 变更」都在同一个事务内完成，且用 SELECT ... FOR UPDATE
锁住相关行，禁止「先查后改」，否则并发借同一本书会超借。
"""
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import (
    ALLOW_BORROW_WHEN_OVERDUE,
    BORROW_DAYS,
    BORROW_LIMIT,
    MAX_RENEW_TIMES,
    RENEW_DAYS,
)
from ..models import Book, BookCopy, BorrowRecord, User
from ..schemas import BorrowRecordOut

STATUS_CN = {
    "available": "在馆可借",
    "borrowed": "已借出",
    "lost": "遗失",
    "repair": "修补中",
    "discarded": "已注销",
}


def _now() -> datetime:
    return datetime.now()


def borrow_limit_of(user: User) -> int:
    if user.max_borrow is not None:
        return user.max_borrow
    return BORROW_LIMIT.get(user.role, 0)


def borrow_days_of(user: User) -> int:
    return BORROW_DAYS.get(user.role, 30)


def overdue_days_of(rec: BorrowRecord, now: datetime | None = None) -> int:
    """按自然日计算逾期天数，未逾期返回 0。"""
    if rec.return_at is not None:
        return 0
    now = now or _now()
    delta = (now.date() - rec.due_at.date()).days
    return delta if delta > 0 else 0


def to_out(rec: BorrowRecord, now: datetime | None = None) -> BorrowRecordOut:
    now = now or _now()
    return BorrowRecordOut(
        id=rec.id,
        book_id=rec.book_id,
        copy_id=rec.copy_id,
        barcode=rec.copy.barcode if rec.copy else "",
        title=rec.book.title if rec.book else "",
        author=rec.book.author if rec.book else None,
        username=rec.user.username if rec.user else "",
        real_name=rec.user.real_name if rec.user else "",
        borrow_at=rec.borrow_at,
        due_at=rec.due_at,
        return_at=rec.return_at,
        renew_count=rec.renew_count,
        status=rec.status,
        overdue_days=overdue_days_of(rec, now),
    )


def _resolve_reader(db: Session, actor: User, username: str | None) -> User:
    """确定这次借书借给哪位读者。

    填了自己的账号（或留空）= 给自己借，谁都可以。
    填别人的账号 = 代借，只开放给管理员：教师账号一旦泄露，
    拿到它的人就能拿任意读者的名义借书。

    username 与 actor.username 相同的情况按「给自己借」处理，
    免得管理员之外的人顺手填了自己的工号反而被拒。
    """
    wanted = (username or "").strip()

    if not wanted or wanted == actor.username:
        return actor

    if actor.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="只有管理员可以代他人借书"
        )

    reader = db.execute(
        select(User).where(User.username == wanted)
    ).scalar_one_or_none()
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"用户 {username} 不存在"
        )
    if reader.status != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="该账号已被禁用"
        )
    return reader


def borrow(
    db: Session,
    actor: User,
    barcode: str,
    username: str | None = None,
) -> BorrowRecord:
    barcode = barcode.strip()
    reader = _resolve_reader(db, actor, username)

    # 先锁读者行：串行化同一读者的并发借书，避免绕过上限
    reader = db.execute(
        select(User).where(User.id == reader.id).with_for_update()
    ).scalar_one()

    now = _now()

    # 有逾期未还的书就拒绝（可在 config 里关掉）
    if not ALLOW_BORROW_WHEN_OVERDUE:
        overdue = db.execute(
            select(func.count())
            .select_from(BorrowRecord)
            .where(
                BorrowRecord.user_id == reader.id,
                BorrowRecord.return_at.is_(None),
                BorrowRecord.due_at < now,
            )
        ).scalar_one()
        if overdue:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"您有 {overdue} 册图书已逾期未还，请先归还后再借",
            )

    limit = borrow_limit_of(reader)
    active = db.execute(
        select(func.count())
        .select_from(BorrowRecord)
        .where(BorrowRecord.user_id == reader.id, BorrowRecord.return_at.is_(None))
    ).scalar_one()
    if active >= limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"已达到可借上限（{limit} 册），请先归还部分图书",
        )

    # 锁副本行：同一册书的并发借书在这里排队
    copy = db.execute(
        select(BookCopy).where(BookCopy.barcode == barcode).with_for_update()
    ).scalar_one_or_none()
    if copy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"条码 {barcode} 不存在，请确认是否为本馆图书",
        )
    if copy.status == "borrowed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="这本书已经被借出，请先归还"
        )
    if copy.status != "available":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"这本书当前状态为「{STATUS_CN.get(copy.status, copy.status)}」，不可借出",
        )

    # 扣减在馆库存，带 guard；受影响行数为 0 说明库存不一致，回滚
    res = db.execute(
        update(Book)
        .where(Book.id == copy.book_id, Book.available_copies > 0)
        .values(available_copies=Book.available_copies - 1)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount == 0:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该书的在馆库存不足，借书失败，请到服务台核对",
        )

    copy.status = "borrowed"
    rec = BorrowRecord(
        copy_id=copy.id,
        book_id=copy.book_id,
        user_id=reader.id,
        borrow_at=now,
        due_at=now + timedelta(days=borrow_days_of(reader)),
        status="borrowed",
        operator_id=None if actor.id == reader.id else actor.id,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def borrow_by_book(db: Session, actor: User, book_id: int) -> BorrowRecord:
    """自助借书：按书目借一册在馆副本，读者不用自己认条码。

    先把这本书当前在馆的条码全取出来，再逐个交给 borrow()。如果第一册刚被别人
    抢先借走（borrow 会报「已被借出」），就顺次试下一册，不至于因为一本走空的
    副本让整个请求失败。加锁和库存扣减仍然只在 borrow() 里做，这里不重复实现。
    """
    barcodes = db.scalars(
        select(BookCopy.barcode)
        .where(BookCopy.book_id == book_id, BookCopy.status == "available")
        .order_by(BookCopy.id)
    ).all()
    if not barcodes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这本书当前没有可借的副本，请稍后再试",
        )

    for barcode in barcodes:
        try:
            return borrow(db, actor, barcode)
        except HTTPException as exc:
            # 只有「这一册刚被借走」才值得换下一册，其它错误（上限、逾期）直接抛出去
            if exc.status_code != status.HTTP_409_CONFLICT:
                raise

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="这本书的可借副本刚被借完了，请稍后再试",
    )


def return_book(
    db: Session,
    actor: User,
    barcode: str,
    username: str | None = None,
) -> BorrowRecord:
    barcode = barcode.strip()

    copy = db.execute(
        select(BookCopy).where(BookCopy.barcode == barcode).with_for_update()
    ).scalar_one_or_none()
    if copy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"条码 {barcode} 不存在"
        )

    rec = db.execute(
        select(BorrowRecord)
        .where(BorrowRecord.copy_id == copy.id, BorrowRecord.return_at.is_(None))
        .with_for_update()
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="这本书当前不在借出状态，无需归还"
        )

    if actor.role == "student" and rec.user_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="不能归还他人借出的图书"
        )

    now = _now()
    rec.return_at = now
    rec.status = "returned"
    copy.status = "available"

    # 加回库存，用 LEAST 封顶，防止长期漂移后超过总册数
    db.execute(
        update(Book)
        .where(Book.id == copy.book_id)
        .values(
            available_copies=func.least(Book.available_copies + 1, Book.total_copies)
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(rec)
    return rec


def renew(db: Session, actor: User, record_id: int) -> BorrowRecord:
    rec = db.execute(
        select(BorrowRecord).where(BorrowRecord.id == record_id).with_for_update()
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="借阅记录不存在")

    if actor.role == "student" and rec.user_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="不能续借他人借出的图书"
        )
    if rec.return_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该记录已归还，无需续借"
        )
    if rec.renew_count >= MAX_RENEW_TIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"每册最多续借 {MAX_RENEW_TIMES} 次，已用完",
        )

    now = _now()
    if rec.due_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="图书已逾期，不能续借，请先归还"
        )

    rec.due_at = rec.due_at + timedelta(days=RENEW_DAYS)
    rec.renew_count += 1
    db.commit()
    db.refresh(rec)
    return rec
