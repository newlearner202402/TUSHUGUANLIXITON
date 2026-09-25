"""馆藏与借阅统计。"""
from datetime import datetime, time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_staff
from ..models import Book, BookCopy, BorrowRecord, User
from ..schemas import StatsOverview

router = APIRouter(prefix="/stats", tags=["统计"])


@router.get("/overview", response_model=StatsOverview, summary="总览统计")
def overview(_: User = Depends(require_staff), db: Session = Depends(get_db)):
    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)

    def count(stmt) -> int:
        return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    total_books = count(select(Book.id))
    total_copies = count(select(BookCopy.id))
    available_copies = count(
        select(BookCopy.id).where(BookCopy.status == "available")
    )
    borrowed_copies = count(select(BookCopy.id).where(BookCopy.status == "borrowed"))

    total_users = count(select(User.id))
    total_students = count(select(User.id).where(User.role == "student"))
    total_teachers = count(select(User.id).where(User.role == "teacher"))

    active_borrows = count(
        select(BorrowRecord.id).where(BorrowRecord.return_at.is_(None))
    )
    overdue_count = count(
        select(BorrowRecord.id).where(
            BorrowRecord.return_at.is_(None), BorrowRecord.due_at < now
        )
    )
    today_borrows = count(
        select(BorrowRecord.id).where(BorrowRecord.borrow_at >= today_start)
    )
    today_returns = count(
        select(BorrowRecord.id).where(BorrowRecord.return_at >= today_start)
    )

    return StatsOverview(
        total_books=total_books,
        total_copies=total_copies,
        available_copies=available_copies,
        borrowed_copies=borrowed_copies,
        total_users=total_users,
        total_students=total_students,
        total_teachers=total_teachers,
        active_borrows=active_borrows,
        overdue_count=overdue_count,
        today_borrows=today_borrows,
        today_returns=today_returns,
    )
