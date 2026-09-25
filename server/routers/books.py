"""图书检索与编目、馆藏副本管理。"""
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_staff
from ..models import Book, BookCopy, BorrowRecord, Category, User
from ..schemas import (
    BatchDeleteIn,
    BatchDeleteResult,
    BatchSkippedItem,
    BookDetail,
    BookIn,
    BookOut,
    BookUpdate,
    CopyCreateIn,
    CopyOut,
    CopyUpdateIn,
    Page,
)

router = APIRouter(prefix="/books", tags=["图书"])
copies_router = APIRouter(prefix="/copies", tags=["馆藏副本"])

MAX_BARCODE_SUFFIX = 999999


def _category_names(db: Session) -> dict[str, str]:
    rows = db.execute(select(Category.code, Category.name)).all()
    return {code: name for code, name in rows}


def _to_out(book: Book, names: dict[str, str]) -> BookOut:
    return BookOut(
        id=book.id,
        isbn=book.isbn,
        title=book.title,
        author=book.author,
        publisher=book.publisher,
        pub_date=book.pub_date,
        price=float(book.price) if book.price is not None else None,
        clc_code=book.clc_code,
        summary=book.summary,
        total_copies=book.total_copies,
        available_copies=book.available_copies,
        category_name=names.get(book.clc_code) if book.clc_code else None,
    )


def recount_book(db: Session, book_id: int) -> None:
    """按副本实际状态重算冗余计数。管理端增删副本、改状态后调用。

    会话是 autoflush=False，所以这里必须先 flush：否则刚删掉的副本、刚改的状态
    还没落库，重算出来的数会偏（副本数虚高、在馆数虚高）。
    """
    db.flush()
    total = (
        db.scalar(
            select(func.count()).select_from(BookCopy).where(BookCopy.book_id == book_id)
        )
        or 0
    )
    available = (
        db.scalar(
            select(func.count())
            .select_from(BookCopy)
            .where(BookCopy.book_id == book_id, BookCopy.status == "available")
        )
        or 0
    )
    db.execute(
        update(Book)
        .where(Book.id == book_id)
        .values(total_copies=total, available_copies=available)
        .execution_options(synchronize_session=False)
    )


def _clean_prefix(raw: str) -> str:
    """条码前缀只保留字母数字，避免 LIKE 通配符污染。"""
    cleaned = re.sub(r"[^0-9A-Za-z]", "", raw).upper()
    return cleaned or "GEN"


def _gen_barcodes(db: Session, prefix: str, count: int) -> list[str]:
    """按 `前缀 + 6 位流水` 生成不重复的馆藏条码。"""
    prefix = _clean_prefix(prefix)
    used = (
        db.execute(
            select(BookCopy.barcode).where(BookCopy.barcode.like(f"{prefix}%"))
        )
        .scalars()
        .all()
    )
    start = 0
    plen = len(prefix)
    for barcode in used:
        suffix = barcode[plen:]
        if suffix.isdigit():
            start = max(start, int(suffix))
    if start + count > MAX_BARCODE_SUFFIX:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"前缀 {prefix} 的条码流水号已用尽，请换一个前缀",
        )
    return [f"{prefix}{start + i + 1:06d}" for i in range(count)]


def _add_copies(db: Session, book: Book, count: int, location: str | None, prefix: str | None):
    if count <= 0:
        return []
    barcodes = _gen_barcodes(db, prefix or book.clc_code or "GEN", count)
    copies = [
        BookCopy(book_id=book.id, barcode=bc, location=location, status="available")
        for bc in barcodes
    ]
    db.add_all(copies)
    db.flush()
    return copies


# ---------------- 检索 ----------------
@router.get("", response_model=Page, summary="检索图书")
def list_books(
    q: str | None = Query(default=None, description="书名/作者/出版社/ISBN 关键词"),
    category: str | None = Query(default=None, description="中图法分类号前缀，如 TP"),
    isbn: str | None = None,
    only_available: bool = Query(default=False, description="只看有在馆副本的"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Book)
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Book.title.like(keyword),
                Book.author.like(keyword),
                Book.publisher.like(keyword),
                Book.isbn.like(keyword),
                Book.clc_code.like(keyword),
            )
        )
    if category:
        stmt = stmt.where(Book.clc_code.like(f"{category.strip()}%"))
    if isbn:
        stmt = stmt.where(Book.isbn == isbn.strip())
    if only_available:
        stmt = stmt.where(Book.available_copies > 0)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(Book.id.desc()).offset((page - 1) * size).limit(size)
        )
        .scalars()
        .all()
    )
    names = _category_names(db)
    return Page(
        total=total, page=page, size=size, items=[_to_out(b, names) for b in rows]
    )


@router.get("/by-barcode/{barcode}", response_model=BookDetail, summary="按馆藏条码查书")
def get_by_barcode(
    barcode: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    copy = db.execute(
        select(BookCopy).where(BookCopy.barcode == barcode.strip())
    ).scalar_one_or_none()
    if copy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"条码 {barcode} 不存在"
        )
    book = db.get(Book, copy.book_id)
    names = _category_names(db)
    detail = BookDetail(**_to_out(book, names).model_dump())
    detail.copies_list = [CopyOut.model_validate(c) for c in book.copies]
    return detail


@router.get("/{book_id}", response_model=BookDetail, summary="图书详情")
def get_book(
    book_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图书不存在")
    names = _category_names(db)
    detail = BookDetail(**_to_out(book, names).model_dump())
    detail.copies_list = [CopyOut.model_validate(c) for c in book.copies]
    return detail


# ---------------- 编目 ----------------
@router.post("", response_model=BookDetail, summary="图书录入")
def create_book(
    payload: BookIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    if payload.clc_code:
        exists = db.execute(
            select(Category).where(Category.code == payload.clc_code)
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"中图法分类号 {payload.clc_code} 不存在，请先新增分类",
            )

    book = Book(
        isbn=payload.isbn or None,
        title=payload.title.strip(),
        author=payload.author,
        publisher=payload.publisher,
        pub_date=payload.pub_date,
        price=payload.price,
        clc_code=payload.clc_code or None,
        summary=payload.summary,
        total_copies=0,
        available_copies=0,
    )
    db.add(book)
    db.flush()

    _add_copies(db, book, payload.copies, payload.location, None)
    recount_book(db, book.id)
    db.commit()
    db.refresh(book)

    names = _category_names(db)
    detail = BookDetail(**_to_out(book, names).model_dump())
    detail.copies_list = [CopyOut.model_validate(c) for c in book.copies]
    return detail


@router.put("/{book_id}", response_model=BookOut, summary="修改图书信息")
def update_book(
    book_id: int,
    payload: BookUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图书不存在")

    data = payload.model_dump(exclude_unset=True)
    if data.get("clc_code"):
        exists = db.execute(
            select(Category).where(Category.code == data["clc_code"])
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"中图法分类号 {data['clc_code']} 不存在，请先新增分类",
            )
    for field, value in data.items():
        setattr(book, field, value)
    db.commit()
    db.refresh(book)
    return _to_out(book, _category_names(db))


def _delete_block_reason(db: Session, book_id: int) -> str | None:
    """返回不能删除的原因；可以删则返回 None。单本与批量删除共用。

    只有在借（未归还）的副本才会拦住删除；已经归还的历史不拦，
    但会在 _purge_book 里跟着书一起删掉。
    """
    active = (
        db.scalar(
            select(func.count())
            .select_from(BorrowRecord)
            .where(BorrowRecord.book_id == book_id, BorrowRecord.return_at.is_(None))
        )
        or 0
    )
    if active:
        return f"当前还有 {active} 册未归还，等全部还清后才能删除"
    return None


def _purge_book(db: Session, book: Book) -> None:
    """删书：先清掉它的借阅历史，再删书本身（副本由 ORM 级联删除）。

    borrow_records.book_id 没有级联，不先清会被外键拦住。
    """
    db.execute(
        delete(BorrowRecord)
        .where(BorrowRecord.book_id == book.id)
        .execution_options(synchronize_session=False)
    )
    db.delete(book)


@router.delete("/{book_id}", summary="删除图书（有未归还副本时不能删）")
def delete_book(
    book_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图书不存在")

    reason = _delete_block_reason(db, book_id)
    if reason:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该书{reason}",
        )
    _purge_book(db, book)
    db.commit()
    return {"ok": True, "message": "图书已删除"}


@router.post("/batch-delete", response_model=BatchDeleteResult, summary="批量删除图书")
def batch_delete_books(
    payload: BatchDeleteIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    """能删的就删，删不了的跳过并说明原因，返回两边的清单。

    可删的条件：没有未归还的副本。已归还的借阅历史会被一并删除。
    """
    deleted = 0
    skipped: list[BatchSkippedItem] = []
    seen: set[int] = set()

    for book_id in payload.ids:
        if book_id in seen:
            continue
        seen.add(book_id)

        book = db.get(Book, book_id)
        if book is None:
            skipped.append(
                BatchSkippedItem(id=book_id, title=None, reason="图书不存在")
            )
            continue

        reason = _delete_block_reason(db, book_id)
        if reason:
            skipped.append(
                BatchSkippedItem(id=book_id, title=book.title, reason=reason)
            )
            continue

        _purge_book(db, book)
        deleted += 1

    db.commit()
    return BatchDeleteResult(deleted=deleted, skipped=skipped)


# ---------------- 副本 ----------------
@router.post("/{book_id}/copies", response_model=list[CopyOut], summary="增加馆藏副本")
def add_copies(
    book_id: int,
    payload: CopyCreateIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图书不存在")

    copies = _add_copies(db, book, payload.count, payload.location, payload.barcode_prefix)
    recount_book(db, book_id)
    db.commit()
    for c in copies:
        db.refresh(c)
    return [CopyOut.model_validate(c) for c in copies]


@copies_router.put("/{copy_id}", response_model=CopyOut, summary="修改副本状态/位置")
def update_copy(
    copy_id: int,
    payload: CopyUpdateIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    copy = db.get(BookCopy, copy_id)
    if copy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="副本不存在")

    if payload.status is not None:
        allowed = {"available", "borrowed", "lost", "repair", "discarded"}
        if payload.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"状态只能是 {'/'.join(sorted(allowed))} 之一",
            )
        if copy.status == "borrowed" and payload.status != "borrowed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该副本正在借出中，请先办理归还再改状态",
            )
        copy.status = payload.status
    if payload.location is not None:
        copy.location = payload.location

    recount_book(db, copy.book_id)
    db.commit()
    db.refresh(copy)
    return CopyOut.model_validate(copy)


@copies_router.delete("/{copy_id}", summary="删除副本（借出中不能删）")
def delete_copy(
    copy_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    copy = db.get(BookCopy, copy_id)
    if copy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="副本不存在")
    if copy.status == "borrowed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该副本正在借出中，请先办理归还再删除",
        )

    # 借阅记录没有级联，先把该副本的历史清掉，否则外键会拦住删除。
    # 与删书同一套规则：已归还的历史不拦人，跟着副本一起删。
    db.execute(
        delete(BorrowRecord)
        .where(BorrowRecord.copy_id == copy_id)
        .execution_options(synchronize_session=False)
    )
    book_id = copy.book_id
    db.delete(copy)
    recount_book(db, book_id)
    db.commit()
    return {"ok": True, "message": "副本已删除"}
